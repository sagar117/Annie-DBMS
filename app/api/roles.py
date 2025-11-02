from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import db, models, schemas

router = APIRouter(prefix="/api/roles", tags=["roles"])


def get_db():
    d = db.SessionLocal()
    try:
        yield d
    finally:
        d.close()


@router.post("/", response_model=schemas.RoleOut)
def create_role(role_in: schemas.RoleCreate, db_session: Session = Depends(get_db)):
    org = db_session.query(models.Organization).filter(models.Organization.id == role_in.org_id).first()
    if not org:
        raise HTTPException(status_code=400, detail="Org not found")

    role = models.Role(
        org_id=role_in.org_id,
        first_name=role_in.first_name,
        last_name=role_in.last_name,
        role=role_in.role,
        email=str(role_in.email) if role_in.email else None,
        phone=role_in.phone,
        password=role_in.password,
        address=role_in.address,
    )
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    return role


@router.get("/", response_model=List[schemas.RoleOut])
def list_roles(
    org_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=1000),
    db_session: Session = Depends(get_db),
):
    q = db_session.query(models.Role)
    if org_id is not None:
        q = q.filter(models.Role.org_id == org_id)
    items = q.offset((page - 1) * limit).limit(limit).all()
    return items


@router.get("/{role_id}", response_model=schemas.RoleOut)
def get_role(role_id: int, db_session: Session = Depends(get_db)):
    r = db_session.query(models.Role).filter(models.Role.id == role_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Role not found")
    return r


@router.put("/{role_id}", response_model=schemas.RoleOut)
def update_role(role_id: int, payload: Dict[str, Any], db_session: Session = Depends(get_db)):
    role = db_session.query(models.Role).filter(models.Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    allowed = {"first_name", "last_name", "role", "email", "phone", "password", "address", "org_id"}
    updated = False
    for k, v in payload.items():
        if k in allowed:
            setattr(role, k, v)
            updated = True

    if updated:
        db_session.add(role)
        db_session.commit()
        db_session.refresh(role)

    return role


@router.delete("/{role_id}")
def delete_role(role_id: int, db_session: Session = Depends(get_db)):
    role = db_session.query(models.Role).filter(models.Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    db_session.delete(role)
    db_session.commit()
    return {"ok": True, "role_id": role_id}
