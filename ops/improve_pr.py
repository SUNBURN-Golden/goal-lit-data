#!/usr/bin/env python3
"""improve 단계가 만든 안전 수정(changes-*.json)을 별도 작업 트리에 적용 → 테스트 → 브랜치 푸시 → PR → (테스트·CI 통과 시) 병합 → 실행 체크아웃 갱신.
main 에 직접 푸시하지 않는다. 테스트가 실패하면 PR 을 만들지 않는다. 마지막 줄에 PR URL 또는 사유를 출력한다."""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(os.environ.get("GLKG_SRC_REPO", "/workspace/gl-data/goal-lit-data"))
RUN = Path(os.environ.get("GLKG_REPO", "/workspace/gl-data/run"))
WT = Path(os.environ.get("GLKG_IMPROVE_WT", "/workspace/gl-data/improve-wt"))


def sh(*a, cwd=None, check=True):
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True, timeout=900)
    if check and r.returncode:
        raise SystemExit(f"fail: {' '.join(a[:3])}: {r.stderr[-300:]}")
    return r


def apply(changes, root: Path):
    bo_p = root / "sources" / "backoff.json"
    bo = json.loads(bo_p.read_text())
    bo.setdefault("domains", {}).update(changes.get("backoff_add", {}))
    bo_p.write_text(json.dumps(bo, ensure_ascii=False, indent=1) + "\n")
    if changes.get("registry_enable"):
        rp = root / "sources" / "registry.json"
        reg = json.loads(rp.read_text())
        for s in reg["sources"]:
            if s["key"] in changes["registry_enable"]:
                s["support_level"] = "supported"
                s["collectable_fields"] = s.pop("collectable_fields_when_open", s.get("collectable_fields", []))
                s["unsupported_scope"] = "대량 덤프·고빈도 조회(일일 상한 공유), 본문"
        rp.write_text(json.dumps(reg, ensure_ascii=False, indent=1) + "\n")


def main(ch_path, prop_path):
    changes = json.loads(Path(ch_path).read_text())
    day = Path(ch_path).stem.replace("changes-", "")
    br = f"improve/{day}"
    sh("git", "fetch", "-q", "origin", cwd=REPO)
    if WT.exists():
        sh("git", "worktree", "remove", "--force", str(WT), cwd=REPO, check=False)
    sh("git", "worktree", "add", "-B", br, str(WT), "origin/main", cwd=REPO)
    try:
        apply(changes, WT)
        d = WT / "docs" / "improve"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"proposal-{day}.md").write_text(Path(prop_path).read_text())
        t = sh(sys.executable, "-m", "pytest", "-q", cwd=WT, check=False)
        if t.returncode:
            print(f"tests_failed_no_pr: {t.stdout.strip().splitlines()[-1:]}")
            return 2
        sh("git", "add", "-A", cwd=WT)
        if not sh("git", "status", "--porcelain", cwd=WT).stdout.strip():
            print("no_changes")
            return 0
        sh("git", "-c", "user.name=Goal Lit 운영", "-c", "user.email=goallit@soulbound.co.kr", "commit", "-qm", f"improve {day}: 안전 수정(백오프/출처 재허용) + 제안", cwd=WT)
        sh("git", "push", "-q", "-f", "origin", br, cwd=WT)
        body = f"자동 개선 단계. 로컬 테스트 통과: {t.stdout.strip().splitlines()[-1]}\n\n" + Path(prop_path).read_text()[:60000]
        r = sh("gh", "pr", "create", "--base", "main", "--head", br, "--title", f"improve {day}: 안전 수정", "--body", body, cwd=WT, check=False)
        url = (r.stdout.strip() or r.stderr.strip()).splitlines()[-1]
        if "github.com" not in url:
            print(f"pr_create_failed: {url}")
            return 3
        c = sh("gh", "pr", "checks", url, "--watch", "--fail-fast", cwd=WT, check=False)
        no_ci = "no checks" in (c.stdout + c.stderr).lower()
        if c.returncode and not no_ci:
            print(f"{url} (CI 실패 → 병합 안 함)")
            return 4
        sh("gh", "pr", "merge", url, "--merge", "--delete-branch", cwd=WT)
        sh("git", "fetch", "-q", "origin", cwd=RUN, check=False)
        sh("git", "checkout", "-q", "--detach", "origin/main", cwd=RUN, check=False)  # 실행 체크아웃은 분리 HEAD
        print(f"{url} merged")
        return 0
    finally:
        sh("git", "worktree", "remove", "--force", str(WT), cwd=REPO, check=False)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
