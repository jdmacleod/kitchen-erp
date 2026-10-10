"""The ``kitchen-erp-brands/1`` bundle, and what importing one reports (spec 16, 2R).

The bundle is built by the kitchen-erp-brands dataset's tools, which check it far
more closely (sources, unique names across families, non-overlapping prefixes).
These models are strict, so a file with a field this code doesn't know is refused
rather than half read, and they check what import relies on: keys, kinds, tiers,
and dates as strings.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

FORMAT = "kitchen-erp-brands/1"
FORMATS = (FORMAT,)
LICENSE = "ODbL-1.0"

_SLUG = r"[a-z0-9][a-z0-9-]*"
FamilyKey = Annotated[str, Field(pattern=rf"^{_SLUG}$", max_length=80)]
MemberKey = Annotated[str, Field(pattern=rf"^{_SLUG}/{_SLUG}$", max_length=160)]
Wikidata = Annotated[str, Field(pattern=r"^Q[0-9]+$", max_length=20)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
PartialDate = Annotated[str, Field(pattern=r"^\d{4}(-\d{2}(-\d{2})?)?$")]
Domain = Annotated[str, Field(pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$", max_length=253)]
Tier = Literal["value", "standard", "premium", "organic", "natural", "prepared", "specialty"]
FamilyKind = Literal["retailer", "wholesaler", "cooperative"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceEntry(_Strict):
    url: Annotated[str, Field(pattern=r"^https://", max_length=1000)]
    checked: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class Owner(_Strict):
    name: Name
    wikidata: Wikidata | None = None


class BannerEntry(_Strict):
    key: MemberKey
    name: Name
    wikidata: Wikidata | None = None
    domains: list[Domain] = Field(default_factory=list)
    since: PartialDate | None = None
    until: PartialDate | None = None
    sources: list[SourceEntry] = Field(default_factory=list)


class BrandEntry(_Strict):
    key: MemberKey
    name: Name
    aliases: list[Name] = Field(default_factory=list)
    tier: Tier
    categories: list[Annotated[str, Field(max_length=32)]] = Field(default_factory=list)
    since: PartialDate | None = None
    until: PartialDate | None = None
    replaced_by: MemberKey | None = None
    note: Annotated[str, Field(max_length=500)] | None = None
    sources: list[SourceEntry] = Field(default_factory=list)


class PrefixEntry(_Strict):
    prefix: Annotated[str, Field(pattern=r"^\d{6,12}$")]
    also: list[FamilyKey] = Field(default_factory=list)
    sources: list[SourceEntry] = Field(default_factory=list)


class FamilyEntry(_Strict):
    key: FamilyKey
    name: Name
    kind: FamilyKind
    wikidata: Wikidata | None = None
    owner: Owner | None = None
    note: Annotated[str, Field(max_length=1000)] | None = None
    sources: list[SourceEntry] = Field(default_factory=list)
    banners: list[BannerEntry] = Field(default_factory=list)
    carries: list[MemberKey] = Field(default_factory=list)
    brands: list[BrandEntry] = Field(default_factory=list)
    gs1_prefixes: list[PrefixEntry] = Field(default_factory=list)


class BundleSource(_Strict):
    name: str
    generated_at: str
    commit: str | None = None


class BrandFile(_Strict):
    format: Literal["kitchen-erp-brands/1"]
    license: Literal["ODbL-1.0"]
    source: BundleSource | None = None
    families: list[FamilyEntry]


# --- import report ------------------------------------------------------------


class BrandImportCounts(BaseModel):
    families_created: int = 0
    families_updated: int = 0
    brands_created: int = 0
    brands_updated: int = 0
    unchanged: int = 0
    kept: int = 0  # fields a person edited, left as they are


class Refusal(BaseModel):
    """One line of the file that was not imported, and why, in plain words."""

    key: str
    reason: str


class VendorLink(BaseModel):
    vendor: str
    family: str | None
    matched_by: Literal["wikidata", "domain", "name"] | None = None
    reason: str | None = None  # why nothing was linked, when a person is needed


class BrandImportReport(BaseModel):
    dry_run: bool
    commit: str | None = None
    counts: BrandImportCounts
    # Families and brands already here that the file no longer lists: kept, never deleted.
    missing: list[str] = Field(default_factory=list)
    refused: list[Refusal] = Field(default_factory=list)
    vendors_linked: list[VendorLink] = Field(default_factory=list)
    vendors_needing_you: list[VendorLink] = Field(default_factory=list)
    products_linked: int = 0
