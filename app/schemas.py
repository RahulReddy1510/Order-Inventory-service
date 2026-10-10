from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------- Products (storefront + warehouse) ----------

class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    category: str = Field(default="general", max_length=100)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    stock: int = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    """Partial update; stock is changed via the dedicated stock endpoint."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class StockAdjustment(BaseModel):
    """Warehouse stock movement: positive = restock, negative = shrinkage."""
    delta: int

    @field_validator("delta")
    @classmethod
    def non_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("delta must be non-zero")
        return v


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    description: str
    category: str
    price: Decimal
    stock: int
    created_at: datetime
    updated_at: datetime


# ---------- Search ----------

class SearchHit(BaseModel):
    id: int
    sku: str
    name: str
    description: str
    category: str
    price: Decimal
    stock: int


class SearchResult(BaseModel):
    total: int
    items: list[SearchHit]


# ---------- Orders ----------

class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=1000)


class OrderCreate(BaseModel):
    items: list[OrderItemCreate] = Field(min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def unique_products(cls, items: list[OrderItemCreate]) -> list[OrderItemCreate]:
        ids = [i.product_id for i in items]
        if len(ids) != len(set(ids)):
            raise ValueError("each product may appear only once per order")
        return items


class OrderItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    quantity: int
    unit_price: Decimal


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    total_amount: Decimal
    created_at: datetime
    items: list[OrderItemRead]


# ---------- Reporting ----------

class ProductSalesRow(BaseModel):
    product_id: int
    sku: str
    name: str
    category: str
    units_sold: int
    revenue: Decimal
    order_count: int
    current_stock: int
    stock_value: Decimal
    sell_through_pct: float


class CategorySummary(BaseModel):
    category: str
    products: int
    units_sold: int
    revenue: Decimal
    stock_value: Decimal


class SalesReport(BaseModel):
    generated_at: datetime
    total_orders: int
    total_units_sold: int
    total_revenue: Decimal
    average_order_value: Decimal
    total_stock_units: int
    total_stock_value: Decimal
    by_category: list[CategorySummary]
    top_products: list[ProductSalesRow]
    low_stock: list[ProductSalesRow]
