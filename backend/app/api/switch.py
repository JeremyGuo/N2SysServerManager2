from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel

from app.database import get_db, Switch, User, SwitchPort
from validator import getUserAdmin
from logger import logger

router = APIRouter()

# 请求/响应模型
class SwitchCreate(BaseModel):
    name: str
    num_row: int
    num_col: int

class SwitchOut(BaseModel):
    id: int
    name: str
    num_row: int
    num_col: int

@router.post("/add", response_model=SwitchOut, status_code=status.HTTP_201_CREATED)
def add_switch(body: SwitchCreate, db: Session = Depends(get_db), _=Depends(getUserAdmin)):
    db_sw = Switch(name=body.name, num_row=body.num_row, num_col=body.num_col)
    try:
        db.add(db_sw)
        db.commit()
        db.refresh(db_sw)
    except Exception as e:
        logger.error(f"Error adding switch: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to add switch")

    try:
        for i in range(body.num_row):
            for j in range(body.num_col):
                sw_port = SwitchPort(
                    switch_id=db_sw.id,
                    phy_row=i,
                    phy_col=j
                )
                db.add(sw_port)
        db.commit()
    except Exception as e:
        logger.error(f"Error adding switch ports: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to add switch ports")
    logger.info(f"Switch {body.name} added with ID {db_sw.id}")
    return db_sw

@router.get("/list", response_model=List[SwitchOut])
def list_switches(db: Session = Depends(get_db), _=Depends(getUserAdmin)):
    return db.query(Switch).all()