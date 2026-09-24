from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel, Field, model_validator

from app.database import get_db, Switch, User, SwitchPort
from validator import getUserAdmin
from logger import logger

router = APIRouter()

# 请求/响应模型
class SwitchCreate(BaseModel):
    name: str
    num_row: int = Field(gt=0, le=1024, strict=True)
    num_col: int = Field(gt=0, le=1024, strict=True)

    @model_validator(mode="after")
    def valid_dimensions(self):
        if self.num_row * self.num_col > 4096:
            raise ValueError("A switch may have at most 4096 ports")
        return self

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
        db.flush()
        for i in range(body.num_row):
            for j in range(body.num_col):
                sw_port = SwitchPort(
                    switch_id=db_sw.id,
                    phy_row=i,
                    phy_col=j
                )
                db.add(sw_port)
        db.flush()
        result = SwitchOut(id=db_sw.id, name=db_sw.name,
                           num_row=db_sw.num_row, num_col=db_sw.num_col)
        db.commit()
    except Exception as e:
        logger.error(f"Error adding switch ports: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to add switch and ports; no changes were saved")
    logger.info(f"Switch {body.name} added with ID {result.id}")
    return result

@router.get("/list", response_model=List[SwitchOut])
def list_switches(db: Session = Depends(get_db), _=Depends(getUserAdmin)):
    return db.query(Switch).all()