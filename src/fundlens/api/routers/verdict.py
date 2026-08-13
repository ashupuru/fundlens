"""PRD FR-6.1/FR-5.3: recommendation verdicts and the Direct-vs-Regular audit."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.compute.holdings import get_current_holdings
from fundlens.compute.plan_audit import detect_plan_type
from fundlens.compute.verdict import get_all_verdicts

router = APIRouter(tags=["verdicts"])


class TaxImpactResponse(BaseModel):
    computable: bool
    reason: str | None
    stcg_gain_paise: int
    ltcg_gain_paise: int
    ltcg_exemption_available_paise: int
    ltcg_exemption_consumed_paise: int
    taxable_ltcg_paise: int
    tax_owed_paise: int
    exit_load_modeled: bool
    breakeven_note: str


class RubricComponentResponse(BaseModel):
    name: str
    score: float | None
    weight: float
    reason: str | None


class VerdictResponse(BaseModel):
    folio: str
    scheme_key: str
    scheme_name: str
    verdict_type: str
    overall_score: float | None
    confidence_pct: float
    components: list[RubricComponentResponse]
    reasoning: str
    counterfactual: str | None
    tax_impact: TaxImpactResponse | None
    blocked_reason: str | None
    omitted_components: list[str]


@router.get("/verdicts", response_model=list[VerdictResponse])
def verdicts(session: Session = Depends(get_session)) -> list[VerdictResponse]:
    return [
        VerdictResponse(
            folio=v.folio,
            scheme_key=v.scheme_key,
            scheme_name=v.scheme_name,
            verdict_type=v.verdict_type.value,
            overall_score=v.overall_score,
            confidence_pct=v.confidence_pct,
            components=[
                RubricComponentResponse(
                    name=c.name, score=c.score, weight=c.weight, reason=c.reason
                )
                for c in v.components
            ],
            reasoning=v.reasoning,
            counterfactual=v.counterfactual,
            tax_impact=(
                TaxImpactResponse(
                    computable=v.tax_impact.computable,
                    reason=v.tax_impact.reason,
                    stcg_gain_paise=v.tax_impact.stcg_gain_paise,
                    ltcg_gain_paise=v.tax_impact.ltcg_gain_paise,
                    ltcg_exemption_available_paise=v.tax_impact.ltcg_exemption_available_paise,
                    ltcg_exemption_consumed_paise=v.tax_impact.ltcg_exemption_consumed_paise,
                    taxable_ltcg_paise=v.tax_impact.taxable_ltcg_paise,
                    tax_owed_paise=v.tax_impact.tax_owed_paise,
                    exit_load_modeled=v.tax_impact.exit_load_modeled,
                    breakeven_note=v.tax_impact.breakeven_note,
                )
                if v.tax_impact is not None
                else None
            ),
            blocked_reason=v.blocked_reason,
            omitted_components=list(v.omitted_components),
        )
        for v in get_all_verdicts(session)
    ]


class DirectVsRegularFlagResponse(BaseModel):
    folio: str
    scheme_name: str
    amc: str
    plan_type: str


@router.get("/audit/direct-vs-regular", response_model=list[DirectVsRegularFlagResponse])
def direct_vs_regular_audit(
    session: Session = Depends(get_session),
) -> list[DirectVsRegularFlagResponse]:
    return [
        DirectVsRegularFlagResponse(
            folio=h.folio, scheme_name=h.scheme_name, amc=h.amc,
            plan_type=detect_plan_type(h.scheme_name).value,
        )
        for h in get_current_holdings(session)
    ]
