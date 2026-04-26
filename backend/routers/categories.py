from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.db import get_db
from backend.models import Category, HSAExpense
from backend.schemas import CategoryIn, CategoryOut
from config import HSA_CATEGORIES

router = APIRouter(prefix="/api/v1", tags=["categories"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    """Return the merged list: preset defaults + user-added custom categories."""
    db_cats = db.query(Category).order_by(Category.name).all()

    # Build a set of names already in the DB for dedup
    db_names = {c.name for c in db_cats}

    # Start with preset defaults (always present, in config order)
    result = []
    for name in HSA_CATEGORIES:
        match = next((c for c in db_cats if c.name == name), None)
        if match:
            result.append(match)
        else:
            # Preset not yet in DB (e.g. fresh install before bootstrap runs) —
            # return a virtual row so the frontend always sees it.
            result.append(Category(id=0, name=name, is_default=True))

    # Append any user-created custom categories after the defaults
    for c in db_cats:
        if c.name not in HSA_CATEGORIES:
            result.append(c)

    return result


@router.post("/categories", response_model=CategoryOut)
def create_category(payload: CategoryIn, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name cannot be empty")

    # Check against both DB and preset defaults
    existing = db.query(Category).filter(Category.name.ilike(name)).first()
    if existing or name in HSA_CATEGORIES:
        raise HTTPException(status_code=400, detail="Category already exists")

    cat = Category(name=name, is_default=False)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@router.delete("/categories/{cat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(cat_id: int, db: Session = Depends(get_db)):
    cat = db.query(Category).filter(Category.id == cat_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if cat.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete a default category")

    # Reassign any expenses using this category to "Other"
    db.execute(
        text("UPDATE hsa_expenses SET category = :other WHERE category = :old_name"),
        {"other": "Other", "old_name": cat.name}
    )

    db.delete(cat)
    db.commit()
    return
