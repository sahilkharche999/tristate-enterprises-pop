"""Protected HOA list/detail/update endpoints."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..models.hoa import HOACreateRequest, HOADetail, HOAListItem, HOAUpdateRequest
from ..services import hoa_service

router = APIRouter(tags=["HOA"])


@router.get("/hoa", response_model=List[HOAListItem])
async def list_hoa(session: Session = Depends(get_session)) -> List[HOAListItem]:
    return hoa_service.list_hoas(session)


@router.post("/hoa", response_model=HOADetail)
async def create_hoa(
    payload: HOACreateRequest,
    session: Session = Depends(get_session),
) -> HOADetail:
    try:
        return hoa_service.create_hoa(session, payload)
    except ValueError:
        session.rollback()
        raise HTTPException(status_code=409, detail="HOA code or name already exists")
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="HOA code or name already exists")


@router.get("/hoa/{hoa_id}", response_model=HOADetail)
async def get_hoa(hoa_id: int, session: Session = Depends(get_session)) -> HOADetail:
    hoa = hoa_service.get_hoa(session, hoa_id)
    if hoa is None:
        raise HTTPException(status_code=404, detail="HOA not found")
    return hoa


@router.put("/hoa/{hoa_id}", response_model=HOADetail)
async def update_hoa(
    hoa_id: int,
    payload: HOAUpdateRequest,
    session: Session = Depends(get_session),
) -> HOADetail:
    try:
        hoa = hoa_service.update_hoa(session, hoa_id, payload)
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="HOA code or name already exists")
    if hoa is None:
        raise HTTPException(status_code=404, detail="HOA not found")
    return hoa
