from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class UpstreamBaseline:
    repository: str
    tag: str
    commit: str
    archive_sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


UPSTREAM = UpstreamBaseline(
    repository="earendil-works/pi",
    tag="v0.84.4",
    commit="b79e4cc",
    archive_sha256="ca3958559b60f87ee44c84d94df8c3ee0b7eda575370402abb2d0ad9155cde4a",
)
