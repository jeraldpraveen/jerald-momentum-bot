"""Part B routes — universe options, sectors, run the Jerald scoring, advance."""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import config
from ..domain import scoring_formula
from ..schemas.cycle import Cycle
from ..schemas.scanner import (
    FormulaValidateRequest,
    FormulaValidateResponse,
    PartBRunRequest,
    UniverseOptionOut,
)
from ..services import cycle_service, scanner_service

router = APIRouter(prefix="/api", tags=["part-b"])


@router.post("/scanner/validate-formula", response_model=FormulaValidateResponse)
def validate_formula(body: FormulaValidateRequest) -> FormulaValidateResponse:
    """Check a scoring formula without running it (for live UI validation)."""
    formula = body.formula.strip() or scoring_formula.DEFAULT_FORMULA
    try:
        compiled = scoring_formula.compile_formula(formula)
        return FormulaValidateResponse(ok=True, terms=sorted(compiled.used_terms))
    except scoring_formula.FormulaError as exc:
        return FormulaValidateResponse(ok=False, error=str(exc))


@router.get("/universe-options", response_model=list[UniverseOptionOut])
def universe_options() -> list[UniverseOptionOut]:
    return [UniverseOptionOut(id=o.id, label=o.label, note=o.note) for o in config.UNIVERSE_OPTIONS]


@router.get("/universe-sectors", response_model=list[str])
def universe_sectors(ids: list[str] = Query(default=[])) -> list[str]:
    """Distinct sectors across the selected universes (union), for the Sector filter."""
    return scanner_service.list_sectors(ids)


@router.post("/cycles/current/steps/part-b/run", response_model=Cycle)
def run_part_b(body: PartBRunRequest | None = None) -> Cycle:
    """Compute the Jerald score across the universe. Failures land on the step (HTTP 200)."""
    return scanner_service.run(body or PartBRunRequest())


@router.post("/cycles/current/steps/part-b/advance", response_model=Cycle)
def advance_part_b() -> Cycle:
    return cycle_service.advance_part_b()
