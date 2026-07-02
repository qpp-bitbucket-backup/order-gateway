from sqlmodel import Field
from typing import Optional, TYPE_CHECKING
from enum import Enum
from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.schemas.order import Address as AddressSchema


class AddressType(str, Enum):
    """Address type enumeration."""
    DELIVERY = "delivery"
    BILLING = "billing"


class Address(BaseModel, table=True):
    """Address model for storing address information."""

    __tablename__ = "addresses"

    country: Optional[str] = Field(None, description="Country")
    state: Optional[str] = Field(None, description="State/Province")
    city: Optional[str] = Field(None, description="City")
    address1: Optional[str] = Field(None, description="Street address line 1")
    address2: Optional[str] = Field(None, description="Street address line 2")
    postcode: Optional[str] = Field(None, description="Postal code")
    first_name: Optional[str] = Field(None, description="First name")
    last_name: Optional[str] = Field(None, description="Last name")
    phone: Optional[str] = Field(None, description="Phone number")
    mobile: Optional[str] = Field(None, description="Mobile number")
    email: Optional[str] = Field(None, description="Email address")
    company: Optional[str] = Field(None, description="Company name")
    order_id: Optional[str] = Field(None, index=True, description="Associated order ID")
    type: Optional[AddressType] = Field(None, description="Address type: delivery or billing")

    @classmethod
    def from_api_address(
        cls,
        api_address: "AddressSchema",
        order_id: Optional[str] = None
    ) -> "Address":
        """
        Create Address model from Order API Address schema.
        
        Mapping:
        - isoCountry -> country
        - town -> city
        - companyName -> company
        - name -> first_name + last_name (split by space)
        
        Args:
            api_address: Address schema from Order API
            order_id: Optional order ID to associate
            
        Returns:
            Address model instance
        """
        # Split name into first_name and last_name
        first_name = None
        last_name = None
        if api_address.name:
            parts = api_address.name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else None
        
        return cls(
            country=api_address.isoCountry,
            state=api_address.state,
            city=api_address.town,
            address1=api_address.address1,
            postcode=api_address.postcode,
            first_name=first_name,
            last_name=last_name,
            phone=api_address.phone,
            email=api_address.email,
            company=api_address.companyName,
            order_id=order_id
        )
    
    def to_api_address(self) -> "AddressSchema":
        """
        Convert Address model to Order API Address schema.
        
        Mapping:
        - country -> isoCountry
        - city -> town
        - company -> companyName
        - first_name + last_name -> name (joined with space)
        
        Returns:
            Address schema instance
        """
        from app.schemas.order import Address as AddressSchema
        
        # Combine first_name and last_name into name
        name = None
        if self.first_name or self.last_name:
            name_parts = [p for p in [self.first_name, self.last_name] if p]
            name = " ".join(name_parts)
        
        return AddressSchema(
            name=name,
            companyName=self.company,
            address1=self.address1,
            town=self.city,
            state=self.state,
            postcode=self.postcode,
            isoCountry=self.country,
            email=self.email,
            phone=self.phone
        )
