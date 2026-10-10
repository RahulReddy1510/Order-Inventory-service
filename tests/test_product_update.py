from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import ProductUpdate


@pytest.mark.parametrize("field", ["name", "description", "category", "price"])
def test_explicit_null_update_is_rejected(field):
    with pytest.raises(ValidationError, match="cannot be null"):
        ProductUpdate.model_validate({field: None})


def test_omitted_fields_are_not_updated():
    assert ProductUpdate().model_dump(exclude_unset=True) == {}
    assert ProductUpdate(name="Laptop").model_dump(exclude_unset=True) == {
        "name": "Laptop"
    }


def test_valid_empty_description_and_zero_price_are_preserved():
    update = ProductUpdate(description="", price="0.00")
    assert update.model_dump(exclude_unset=True) == {
        "description": "", "price": Decimal("0.00")
    }
