from .discover import DiscoverStage
from .fetch import FetchStage
from .detect import DetectStage
from .extract import ExtractStage
from .resolve import ResolveStage
from .verify import VerifyStage
from .history import HistoryStage
from .recompute import RecomputeStage
from .quality import QualityStage
from .parse import ParseStage
from .seek import SeekStage
from .assess import AssessStage
from .harvest import HarvestStage
from .gaps import GapStage
from .themes import ThemeStage
from .improve import ImproveStage

ORDER = ["discover", "seek", "harvest", "assess", "gaps", "fetch", "parse", "detect", "extract", "themes", "resolve", "verify", "history", "recompute", "quality", "improve"]
STAGES = {
    "discover": DiscoverStage, "fetch": FetchStage, "detect": DetectStage, "extract": ExtractStage, "resolve": ResolveStage,
    "verify": VerifyStage, "history": HistoryStage, "recompute": RecomputeStage, "quality": QualityStage, "parse": ParseStage,
    "seek": SeekStage, "assess": AssessStage, "harvest": HarvestStage, "gaps": GapStage, "themes": ThemeStage, "improve": ImproveStage,
}
