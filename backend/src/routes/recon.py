from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response
from src.config.database import get_db
from src.middlewares.auth import current_user, can_write
from src.models import ReconSheet
from src.schemas.recon import (ReconDecisionInput, ReconReadingsInput, ReconSealInput,
                               ReconSheetInput)
from src.services import recon
router = APIRouter(prefix='/recon')


def _request_id(body_value, header_value):
    value = (header_value or body_value or '').strip()
    return value or None


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    return recon.list_sheets(db)


@router.post('', status_code=201)
def create(data: ReconSheetInput, response: Response, db=Depends(get_db), u=Depends(can_write),
           idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    payload, created = recon.open_sheet(db, data, u, _request_id(data.request_id, idempotency_key))
    if not created:
        response.status_code = 200
    return payload


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    sheet = db.get(ReconSheet, id)
    if not sheet:
        raise HTTPException(404, '双源核对单不存在')
    return recon.detail(db, sheet)


@router.post('/{id}/readings')
def readings(id: int, data: ReconReadingsInput, db=Depends(get_db), u=Depends(can_write),
             idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    return recon.intake(db, id, data, u, _request_id(data.request_id, idempotency_key))


@router.post('/{id}/points/{point_id}/decision')
def decide(id: int, point_id: int, data: ReconDecisionInput, db=Depends(get_db),
           u=Depends(can_write),
           idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    return recon.decide(db, id, point_id, data, u, _request_id(data.request_id, idempotency_key))


@router.get('/{id}/points/{point_id}')
def point(id: int, point_id: int, db=Depends(get_db), u=Depends(current_user)):
    return recon.point_trace(db, id, point_id)


@router.post('/{id}/seal')
def seal(id: int, data: ReconSealInput = Body(default_factory=ReconSealInput),
         db=Depends(get_db), u=Depends(can_write),
         idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    return recon.seal(db, id, data, u, _request_id(data.request_id, idempotency_key))
