"""Unit tests for Address model."""
import pytest
from app.models.address import Address, AddressType


class TestAddressType:
    """Test AddressType enum."""

    def test_address_type_values(self):
        """Test address type values."""
        assert AddressType.DELIVERY.value == "delivery"
        assert AddressType.BILLING.value == "billing"


class TestAddressModel:
    """Test Address model creation and conversion."""

    def test_address_creation_minimal(self):
        """Test creating an address with minimal fields."""
        address = Address()
        assert address.country is None
        assert address.city is None
        assert address.order_id is None
        assert address.type is None

    def test_address_creation_full(self):
        """Test creating an address with all fields."""
        address = Address(
            country="CN",
            state="BT",
            city="Hong Kong",
            address1="Tian Shui Wai",
            address2="Block A",
            postcode="123456",
            first_name="Ivan",
            last_name="Li",
            phone="85288888888",
            mobile="85288888888",
            email="ivanliyp@qpp.com",
            company="Test Company",
            order_id="order-001",
            type=AddressType.DELIVERY,
        )
        assert address.country == "CN"
        assert address.state == "BT"
        assert address.city == "Hong Kong"
        assert address.address1 == "Tian Shui Wai"
        assert address.address2 == "Block A"
        assert address.postcode == "123456"
        assert address.first_name == "Ivan"
        assert address.last_name == "Li"
        assert address.phone == "85288888888"
        assert address.mobile == "85288888888"
        assert address.email == "ivanliyp@qpp.com"
        assert address.company == "Test Company"
        assert address.order_id == "order-001"
        assert address.type == AddressType.DELIVERY

    def test_address_from_api_address(self):
        """Test creating Address from API address schema."""
        from app.schemas.order import Address as AddressSchema
        
        api_address = AddressSchema(
            name="John Doe",
            companyName="Test Corp",
            address1="123 Main St",
            town="Springfield",
            state="IL",
            postcode="62701",
            isoCountry="US",
            email="john@test.com",
            phone="1234567890",
        )
        
        address = Address.from_api_address(api_address, order_id="order-001")
        
        assert address.country == "US"
        assert address.state == "IL"
        assert address.city == "Springfield"
        assert address.address1 == "123 Main St"
        assert address.postcode == "62701"
        assert address.first_name == "John"
        assert address.last_name == "Doe"
        assert address.email == "john@test.com"
        assert address.phone == "1234567890"
        assert address.company == "Test Corp"
        assert address.order_id == "order-001"

    def test_address_from_api_address_single_name(self):
        """Test creating Address from API address with single name."""
        from app.schemas.order import Address as AddressSchema
        
        api_address = AddressSchema(
            name="John",
            address1="123 Main St",
            isoCountry="US",
        )
        
        address = Address.from_api_address(api_address)
        
        assert address.first_name == "John"
        assert address.last_name is None

    def test_address_to_api_address(self):
        """Test converting Address to API address schema."""
        address = Address(
            country="US",
            state="IL",
            city="Springfield",
            address1="123 Main St",
            postcode="62701",
            first_name="John",
            last_name="Doe",
            email="john@test.com",
            phone="1234567890",
            company="Test Corp",
        )
        
        api_address = address.to_api_address()
        
        assert api_address.isoCountry == "US"
        assert api_address.state == "IL"
        assert api_address.town == "Springfield"
        assert api_address.address1 == "123 Main St"
        assert api_address.postcode == "62701"
        assert api_address.name == "John Doe"
        assert api_address.email == "john@test.com"
        assert api_address.phone == "1234567890"
        assert api_address.companyName == "Test Corp"

    def test_address_to_api_address_single_name(self):
        """Test converting Address with single name to API address."""
        address = Address(
            first_name="John",
            isoCountry="US",
        )
        
        api_address = address.to_api_address()
        
        assert api_address.name == "John"

    def test_address_to_api_address_no_name(self):
        """Test converting Address with no name to API address."""
        address = Address(
            isoCountry="US",
        )
        
        api_address = address.to_api_address()
        
        assert api_address.name is None
