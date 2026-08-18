# Siteflow Data Examples

## Orders

There are two types of example responses below:
1. The minimum amount of data required (embedded)
1. The full Siteflow response (linked) 

Variable values in the embedded examples are identified by \<angle brackets\>.

### Order Validation

#### Order Validation Request

**POST** /order/validate

A full example of the Siteflow request can be viewed here:
[Order Validation Request](examples/siteflow_data_examples-order_validation_request.json)

This request contains three Topps Now items with the following print files:

* [ARTF1-16C2S-26TN-0027](artwork/27313.pdf)
* [ARTF1-16C2S-26TN-0026](artwork/27314.pdf)
* [ARTF1-16C2S-26TN-0025](artwork/27316.pdf)

#### Order Validation Response

##### Order Validation Response - Success

At a minimum, the following JSON values are required:
```json
{
    "orderData":
    {
        "sourceOrderId": "<CN-TEST20260811-1_S1055552>"
    }
}
```

A full example of the Siteflow response can be viewed here:
[Order Validation Response - Success](examples/siteflow_data_examples-order_validation_response-success.json)

##### Order Validation Response - Error

At a minimum, the following JSON values are required:
```json
{
    "success": false
    "error":
    {
        "message": "<Source Order ID already exists>"
    }
}
```

A full example of the Siteflow response can be viewed here:
[Order Validation Response - Error](examples/siteflow_data_examples-order_validation_response-error.json)


### Order Creation

#### Order Creation Request

**POST** /order

A full example of the Siteflow request can be viewed here:
[Order Creation Request](examples/siteflow_data_examples-order_creation_request.json)

#### Order Creation Response

##### Order Creation Response - Success

At a minimum, the following JSON values are required:
```json
{
    "_id": "<6a7b481e9d554ec5d53d63f6>"
}
```
A full example of the Siteflow response can be viewed here:
[Order Creation Response - Success](examples/siteflow_data_examples-order_creation_response-success.json)


##### Order Creation Response - Error

At a minimum, the following JSON values are required:
```json
{
    "_id": ""
}
```
This is a special case, where the validation is not checked prior to creation. Siteflow will accept the order and return an `_id` value, but then it will fail validation and the order will be deleted, so it is no longer accessbile by the `_id`. We don't create orders that fail validation, but if it's possible to reply with an empty `_id` value, we think it would be safer than the way Siteflow handles it. If not, we can work with it in the same way that we work with the Siteflow response.

A full example of the Siteflow response can be viewed here:
[Order Creation Response - Error](examples/siteflow_data_examples-order_creation_response-error.json)


### Order Status

#### Order Status Request

**GET** /order/details/<ORDER_ID>?includes[]=shipments

#### Order Status Response

##### Order Status Response - Success

At a minimum, the following JSON values are required:
```json
{
    "order":
    {
        "_id": "<6a7b481e9d554ec5d53d63f6>",
        "orderData":
        {
            "status": "<accepted>"
        }
    },
    "shipments":
    [
        {
            "carrier":
            {
                "code": "<customer>",
                "service": "<shipping>",
                "alias": "<tracked>",
                "serviceId": "<610c8bbd046a4b166ee73947>"
            }
        }
    ]
}
```

If the order has shipped, the following JSON values are required:
```json
{
    "order":
    {
        "_id": "<6a7b481e9d554ec5d53d63f6>",
        "orderData":
        {
            "status": "shipped"
        }
    },
    "orderId": "<6a7b481e9d554ec5d53d63f6>",
    "shipments":
    [
        {
            "carrier":
            {
                "code": "<japanpost>",
                "service": "<Small Packet>",
                "alias": "<yupacket>",
                "serviceId": "<610c8bbd046a4b166ee73947>"
            },
            "shippedDate": "<2026-08-05T10:20:48.080Z>",
            "trackingNumber": "<LP111111111JP>",
            "trackingUrl": ""
        }
    ]
}
```

A full example of the Siteflow response can be viewed here:
[Order Status Response - Success](examples/siteflow_data_examples-order_status_response-success.json)

##### Order Status Response - Error

At a minimum, the following JSON values are required:
```json
{
    "success": false
    "error":
    {
        "message": "<Order not found>"
    }
}
```

A full example of the Siteflow response can be viewed here: 
[Order Status Request - Error](examples/siteflow_data_examples-order_status_response-error.json)




