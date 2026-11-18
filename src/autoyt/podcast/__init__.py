"""AutoYT Podcast Clipping & Publishing Platform package."""
from .acquisition import AcquiredMedia, MediaAcquisition
from .clip_editor import ClipEditor
from .clip_finder import ClipCandidate, ClipFinder
from .discovery import EpisodeDiscovery, PodcastEpisode
from .metadata import ClipMetadata, MetadataGenerator
from .pipeline import PodcastPipeline, ProcessedClipResult
from .publisher import PodcastPublisher
from .rights import RightsChecker, RightsRecord, RightsStatus
from .scheduler import PodcastScheduler
from .sources import PodcastSource, SourceRegistry
from .subtitles import SubtitleGenerator
from .transcription import FullTranscript, Transcriber, TranscriptSegment, WordTimestamp

__all__ = [
    "PodcastSource",
    "SourceRegistry",
    "PodcastEpisode",
    "EpisodeDiscovery",
    "RightsStatus",
    "RightsRecord",
    "RightsChecker",
    "AcquiredMedia",
    "MediaAcquisition",
    "WordTimestamp",
    "TranscriptSegment",
    "FullTranscript",
    "Transcriber",
    "ClipCandidate",
    "ClipFinder",
    "SubtitleGenerator",
    "ClipEditor",
    "ThumbnailGenerator",
    "ClipMetadata",
    "MetadataGenerator",
    "ProcessedClipResult",
    "PodcastPipeline",
    "PodcastPublisher",
    "PodcastScheduler",
]
