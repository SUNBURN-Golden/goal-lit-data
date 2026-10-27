// Goal Lit 지식그래프 데몬(pm2). 단계마다 프로세스 1개: 락·체크포인트·재시도·일일 상한은 python -m glkg 가 처리한다.
// 실행 위치는 리포 체크아웃(GLKG_REPO). DB·원문·락은 리포 밖(GLKG_DATA_DIR)에 둔다. 비밀값 없음.
const REPO = process.env.GLKG_REPO || "/workspace/gl-data/run";
const LOGS = process.env.GLKG_LOG_DIR || "/workspace/lit-hub/collector/logs";
const mk = (stage, interval, offset) => ({
  name: `glkg-${stage}`,
  script: `${REPO}/ops/glkg_daemon.py`,
  args: `${stage} --interval ${interval} --offset ${offset}`,
  interpreter: "python3",
  cwd: REPO,
  autorestart: true,
  restart_delay: 10000,
  max_restarts: 50,
  out_file: `${LOGS}/glkg-${stage}.out.log`,
  error_file: `${LOGS}/glkg-${stage}.err.log`,
  time: true,
  env: { PYTHONUNBUFFERED: "1" },
});
module.exports = {
  apps: [
    mk("seek", 900, 0),       // 질의 조합 생성(네트워크 없음)
    mk("harvest", 60, 5),     // 수집 실행(예산 클래스별, 도메인당 5초 간격은 코드가 보장)
    mk("assess", 300, 10),    // 새 도메인 평가
    mk("gaps", 600, 15),      // 빈칸 탐색·커버리지
    mk("parse", 600, 20),     // 저장 원문 재파싱
    mk("fetch", 1800, 25),    // 기존 수집기 증분 실행(goallit-auto 대체, 같은 일일 200건 공유)
    mk("detect", 1800, 30),
    mk("extract", 900, 35),
    mk("resolve", 900, 40),
    mk("verify", 900, 45),
    mk("history", 900, 50),
    mk("recompute", 1800, 55),
    mk("quality", 3600, 60),
  ],
};
