from .discover import DiscoverStage
from .fetch import FetchStage
from .detect import DetectStage
from .extract import ExtractStage
from .resolve import ResolveStage
from .verify import VerifyStage
from .history import HistoryStage
from .recompute import RecomputeStage
from .quality import QualityStage

ORDER = ["discover", "fetch", "detect", "extract", "resolve", "verify", "history", "recompute", "quality"]
STAGES = {
    "discover": DiscoverStage, "fetch": FetchStage, "detect": DetectStage, "extract": ExtractStage, "resolve": ResolveStage,
    "verify": VerifyStage, "history": HistoryStage, "recompute": RecomputeStage, "quality": QualityStage,
}
