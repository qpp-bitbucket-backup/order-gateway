# toops 对外 API 接口文档（店铺 Open API）

版本：v1.0
删除策略：逻辑删除

---

# 1. 概述

## 1.1 简介

本文档定义店铺（Store）维度的对外开放 API，供外部系统集成以下能力：

- 店铺订单：创建订单、取消订单、修改订单收货/账单地址；
- Webhook 管理：订阅、查询、修改、删除店铺业务事件回调；
- Webhook 事件推送：当订单项状态变更、发货等业务事件发生时，向店铺配置的回调地址推送事件通知。


## 1.2 Base URL

```text
https://{host}/open-api/v1
```

- 版本号固定位于路径中，当前版本为 `v1`；
- 文档中所有路径均为相对 Base URL 的路径。

---

# 2. 通用约定

## 2.1 认证（Authentication）

所有店铺开放 API 都需要在请求头携带店铺访问 Token：

```text
Authorization: Basic {store_token}
```

说明：

- `store_token` 为店铺访问 Token（店铺级）；
- Token 权限范围限定当前店铺：所有接口只能操作该 Token 所属店铺的数据（店铺 id 取自 Token，无需在请求中显式传 `storeId`）；
- 缺少或非法 Token 时返回 `401 Unauthorized`；

curl 示例：

```bash
curl --location \
  --request GET 'https://{host}/open-api/v1/store/webhook' \
  --header 'Authorization: Basic {store_token}'
```

## 2.2 Content-Type

- 请求体：`application/json; charset=utf-8`；
- 响应体：`application/json`。

## 2.3 响应包装（Response Body）

响应正文始终是一个 JSON 对象，包含请求状态 `success` 与操作结果 `data`。`success` 为 `true` 表示操作成功。

```json
{
  "success": true,
  "data": {
    // API 返回值
  }
}
```

## 2.4 错误响应（Error Response）

当 API 调用不成功时，HTTP 状态码不在 200 范围内，`success` 为 `false`：

- 4xx：由请求方提供的信息导致（缺少必填参数、参数不合法、资源不存在、权限不足等）；
- 5xx：QPMN 服务器内部错误。

```json
{
  "success": false,
  "data": {
    "code": "ORDER_ALREADY_CANCELLED",
    "message": "The order has already been cancelled.",
    "path": "open-api/v1/store/orders/321/cancel",
    "timestamp": 1785829541999
  }
}
```
请求参数校验失败
```json
HTTP/1.1 400 Bad Request
{
  "success": false,
  "data": {
    "code": "FIELD_REQUIRED",
    "message": "deliveryAddress is required！",
    "path": "open-api/v1/orders",
    "timestamp": 1785829541999
  }
}
```

Token 缺失或非法时：

```json
HTTP/1.1 401 Unauthorized
{
  "success": false,
  "data": {
    "code": "Unauthorized",
    "message": "Unauthorized",
    "path": "open-api/v1/store/orders/321/cancel",
    "timestamp": 1785829541999
  }
}
```

资源不存在：

```json
HTTP/1.1 404 Not Find
{
  "success": false,
  "data": {
    "code": "NOT_EXISTS",
    "message": "订单321不存在",
    "path": "open-api/v1/store/orders/321/cancel",
    "timestamp": 1785829541999
  }
}
```

> 各接口的具体错误示例见对应接口的「Error Response」小节，错误信息文本以实际返回为准。

## 2.5 分页 / 过滤（Pagination / Filter）

列表类接口使用以下约定：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `page` | int | 是 | 页码，从 1 开始 |
| `size` | int | 是 | 每页数量；小于等于 0 时按 25 处理 |

各列表接口可选的过滤参数以该接口的「Query 参数」为准，均为请求参数、非必填。

多值过滤参数通过重复传参传递，例如：

```text
eventTypes=order_item_received&eventTypes=order_item_audited
```

分页响应字段：

```json
{
  "success": true,
  "data": {
    "pageNumber": 1,
    "pageSize": 20,
    "totalCount": 100,
    "totalPages": 5,
    "content": []
  }
}
```

---

# 3. 店铺订单接口

## 3.1 创建店铺订单（Open API）

### 基本信息

| 项目 | 内容 |
| --- | --- |
| URL | `/open-api/v1/orders` |
| 请求方式 | POST |
| 接口语义 | 创建店铺订单 |
| 是否幂等 | 是 |
| 是否逻辑删除 | 否 |
| 是否版本升级 | 否 |
| 兼容性 | 向后兼容 |

### Header 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| Authorization | String | 是 | 店铺 Token |

### Request Body

```json
{
  "externalId": "202502190042",
  "externalOrderNumber": "202502190042",
  "shippingMethod": "Standard",
  "paymentMethod": "Stripe",
  "currency": "USD",
  "deliveryAddress": {
    "countryCode": "US",
    "countryName": "United States",
    "stateCode": "CA",
    "state": "California",
    "city": "Irvine",
    "address_1": "9906 Research Dr",
    "address_2": "",
    "postCode": "92602",
    "firstName": "Ziyer",
    "lastName": "Wong",
    "phone": "0777-123456",
    "mobile": "18475031246",
    "email": "ziyerwong@qpp.com"
  },
  "billingAddress": {
    "countryCode": "US",
    "countryName": "United States",
    "stateCode": "CA",
    "state": "California",
    "city": "Irvine",
    "address_1": "9906 Research Dr",
    "address_2": "",
    "postCode": "92602",
    "firstName": "Ziyer",
    "lastName": "Wong",
    "phone": "0777-123456",
    "mobile": "18475031246",
    "email": "ziyerwong@qpp.com"
  },
  "priceInfo": {
    "currency": "USD",
    "subtotal": 10.00,
    "discount": 0.00,
    "shipping": 1.00,
    "tax": 0.00
  },
  "items": [
    {
      "externalId": "202502190042-1",
      "unitPrice": 10.00,
      "storeProductId": "266931519",
      "quantity": 1,
      "supplierStockNo":"xxxx",
      "productDesignData": {
        "designData": [
          {
            "code": "NDI3OTc5ODM4LDQyNzk3OTgzOSwxMTYxODA5NTU=",
            "views": [
              {
                "code": "Card_Front",
                "designs": [
                  {
                    "index": 0,
                    "effectImages": [
                      {
                        "effect": "CMYK",
                        "imageUrl": "https://test-qpmn.qppdev.com/file/file/1f05e1a16a25a0bd588fcc2c4d082856.jpeg"
                      }
                    ]
                  }
                ]
              }
            ]
          }
        ],
        "designAttributeValues": [
          {
            "code": "Front",
            "value": "Different"
          }
        ]
      }
    }
  ]
}
```

### Response Body

```json
{
  "success": true,
  "data": {
    "id": 321,
    "externalId": "202502190042",
    "externalOrderNumber": "202502190042",
    "shippingMethod": "Standard",
    "paymentMethod": "Stripe",
    "currency": "USD",
    "deliveryAddress": {
      "countryCode": "US",
      "countryName": "United States",
      "stateCode": "CA",
      "state": "California",
      "city": "Irvine",
      "address_1": "9906 Research Dr",
      "address_2": "",
      "postCode": "92602",
      "firstName": "Ziyer",
      "lastName": "Wong",
      "phone": "0777-123456",
      "mobile": "18475031246",
      "email": "ziyerwong@qpp.com"
    },
    "billingAddress": {
      "countryCode": "US",
      "countryName": "United States",
      "stateCode": "CA",
      "state": "California",
      "city": "Irvine",
      "address_1": "9906 Research Dr",
      "address_2": "",
      "postCode": "92602",
      "firstName": "Ziyer",
      "lastName": "Wong",
      "phone": "0777-123456",
      "mobile": "18475031246",
      "email": "ziyerwong@qpp.com"
    },
    "priceInfo": {
      "currency": "USD",
      "subtotal": 10.00,
      "discount": 0.00,
      "shipping": 1.00,
      "tax": 0.00
    },
    "items": [
      {
        "id": 321,
        "externalId": "202502190042-1",
        "unitPrice": 10.00,
        "storeProductId": "266931519",
        "quantity": 1,
        "supplierStockNo":"xxxx",
        "status": "pending",
      }
    ]
  }
}
```
### 店铺订单项的状态
| 店铺订单项状态 | 触发时机 | status值 |
| --- | --- | --- |
| `待确认状态` | 店铺订单项初始状态，未拼单 | pending |
| `已确认待审核状态` | 拼单且付款后 | received |
| `已审核状态` | partner订单项审核 | reviewed |
| `已生产（待组装）状态` | partner订单项生产完成 | produced |
| `客戶取消状态` | 店铺零售订单被取消 | canceled |
| `不可生产，已取消` | partner订单项因不可生产被QPMN取消 | failed |
| `已发货，待签收状态` | partner订单项发货时 | shipped |

### Error Response

```json
HTTP/1.1 422 Unprocessable Entity  
{
  "success": false,
  "data": {
    "code": "CUSTOMIZED_CONTENT_NOT__MEET_REQUIREMENTS",
    "message": "Card_Back image https://tails-ai.com/playing-back.jpg width/height ratio:0.7466666666666667 is not match require ratio:0.6",
    "path": "open-api/v1/orders",
    "timestamp": 1785829541999
  }
}
```

---

## 3.2 创建店铺订单（旧版接口）

### 基本信息

| 项目 | 内容 |
| --- | --- |
| URL | `/api/orders` |
| 请求方式 | POST |
| 接口语义 | 创建店铺订单 |
| 是否幂等 | 是 |
| 是否逻辑删除 | 否 |
| 是否版本升级 | 否 |
| 兼容性 | 向后兼容 |

### Header 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| Authorization | String | 是 | 店铺 Token |

### Request Body
```json
{
  "thirdOrderId": "cmshqje3a019qnu13fdjc6w23",
  "thirdOrderNumber": "cmshqje3a019qnu13fdjc6w23",
  "billingAddress": {
    "company": null,
    "country": "CA",
    "stateCode": null,
    "state": "Ontario",
    "city": "Burlington",
    "suburb": null,
    "postcode": "L7R 2S5",
    "mobile": null,
    "email": "miloknay@gmail.com",
    "first_name": "Stephanie",
    "last_name": "Miloknay",
    "address_1": "614 Woodland Avenue",
    "address_2": "",
    "phone": "+19052208009"
  },
  "deliveryAddress": {
    "company": null,
    "country": "CA",
    "stateCode": null,
    "state": "Ontario",
    "city": "Burlington",
    "suburb": null,
    "postcode": "L7R 2S5",
    "mobile": null,
    "email": "miloknay@gmail.com",
    "first_name": "Stephanie",
    "last_name": "Miloknay",
    "address_1": "614 Woodland Avenue",
    "address_2": "",
    "phone": "+19052208009"
  },
  "items": [
    {
      "thirdOrderItemId": "1",
      "qty": 1,
      "unitPrice": 142.17,
      "storeProductId": "610129886",
      "supplierStockNo":"xxxx",
      "productInstanceId": null,
      "attributeValues": null,
      "properties": {
        "front design mode": "different",
        "back design mode": "same"
      },
      "customizeProject": {
        "customizeType": "IMAGE",
        "comparisonThumbnail": "https://tails-ai.com/api/storage/oracle/cmshqje3a019qnu13fdjc6w23/print-ready/card-00-awakening.png?cv=1786062932125",
        "designs": [
          {
            "side": "Card_Back",
            "materialPath": "132003012,132017574,116180955",
            "pageContentDesigns": [
              {
                "pageContentIndex": 0,
                "effect": "CMYK",
                "image": "https://tails-ai.com/assets/oracle-back-0.5.jpg"
              }
            ]
          },
          {
            "side": "Card_Front",
            "materialPath": "132003012,132017574,116180955",
            "pageContentDesigns": [
              {
                "pageContentIndex": 0,
                "effect": "CMYK",
                "image": "https://tails-ai.com/api/storage/oracle/cmshqje3a019qnu13fdjc6w23/print-ready/card-00-awakening.png?cv=1786062932125"
              },
              {
                "pageContentIndex": 1,
                "effect": "CMYK",
                "image": "https://tails-ai.com/api/storage/oracle/cmshqje3a019qnu13fdjc6w23/print-ready/card-01-inner-light.png?cv=1786062932125"
              },
              {
                "pageContentIndex": 2,
                "effect": "CMYK",
                "image": "https://tails-ai.com/api/storage/oracle/cmshqje3a019qnu13fdjc6w23/print-ready/card-02-sacred-path.png?cv=1786062932125"
              }
            ]
          }
        ]
      },
      "remark": null
    }
  ],
  "shippingMethod": "Standard",
  "paymentMethod": "Stripe",
  "currency": "USD",
  "status": "processing",
  "orderTotals": [
    {
      "name": "TAX",
      "value": 0.00
    },
    {
      "name": "SHIPPING",
      "value": 0.00
    },
    {
      "name": "SUBTOTAL",
      "value": 142.17
    },
    {
      "name": "ORDER_TOTAL",
      "value": 142.17
    }
  ],
  "datePurchased": "2026-08-07T00:35:33.135Z",
  "remark": null
}
```
### Response Body
```json
{
  "success": true,
  "data": {
    "orderId": 587481,
    "thirdOrderId": "cmshqje3a019qnu13fdjc6w23",
    "orderNumber": "cmshqje3a019qnu13fdjc6w23",
    "orderItems": [
      {
        "orderItemId": 58748171,
        "thirdOrderItemId": "1"
      }
    ]
  }

}
```
### Error Response

```json
HTTP/1.1 400 Bad Request
{
  "success": false,
  "data": {
    "code": "FIELD_REQUIRED",
    "message": "请求参数校验失败",
    "path": "api/store/orders",
    "timestamp": 1785829541999
  }
}
```
```json
HTTP/1.1 422 Unprocessable Entity  
{
  "success": false,
  "data": {
    "code": "CUSTOMIZED_CONTENT_NOT__MEET_REQUIREMENTS",
    "message": "Card_Back image https://tails-ai.com/playing-back.jpg width/height ratio:0.7466666666666667 is not match require ratio:0.6",
    "path": "open-api/v1/orders",
    "timestamp": 1785829541999
  }
}
```

---

## 3.3 取消店铺订单

### 基本信息

| 项目 | 内容 |
| --- | --- |
| URL | `/open-api/v1/orders/{orderId}/cancel` |
| 请求方式 | PUT |
| 接口语义 | 取消店铺订单 |
| 是否幂等 | 是 |
| 是否逻辑删除 | 否 |
| 是否版本升级 | 否 |
| 兼容性 | 向后兼容 |

### 接口校验规则

- `orderId` 必填，且订单必须属于当前 Token 店铺；
- 订单下所有订单项均未处于「reviewed」及之后的状态时才允许取消，否则HTTP状态返回 `422`；
### Header 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| Authorization | String | 是 | 店铺 Token |

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| orderId | Long | 是 | 店铺零售订单 id |

### Response Body

```json
{
  "success": true,
  "data": {
    "id": 321,
    "externalId": "202502190042",
    "externalOrderNumber": "202502190042",
    "shippingMethod": "Standard",
    "paymentMethod": "",
    "currency": "USD",
    "deliveryAddress": {
      "countryCode": "US",
      "stateCode": "CA",
      "city": "Irvine",
      "address_1": "9906 Research Dr",
      "address_2": "",
      "postCode": "92602",
      "firstName": "Ziyer",
      "lastName": "Wong",
      "phone": "0777-123456",
      "mobile": "18475031246",
      "email": "ziyerwong@qpp.com"
    },
    "billingAddress": {
      "countryCode": "US",
      "stateCode": "CA",
      "city": "Irvine",
      "address_1": "9906 Research Dr",
      "address_2": "",
      "postCode": "92602",
      "firstName": "Ziyer",
      "lastName": "Wong",
      "phone": "0777-123456",
      "mobile": "18475031246",
      "email": "ziyerwong@qpp.com"
    },
    "priceInfo": {
      "currency": "USD",
      "subtotal": 10.00,
      "discount": 0.00,
      "shipping": 1.00,
      "tax": 0.00
    },
    "items": [
      {
        "id": 321,
        "externalId": "202502190042-1",
        "unitPrice": 10.00,
        "storeProductId": "266931519",
        "quantity": 1,
        "supplierStockNo": "xxx",
        "status": "canceled"
      }
    ]
  }
}
```

### Error Response

订单存在订单项已处于「reviewed」及之后状态时：

```json
HTTP/1.1 422 Unprocessable Entity  
{
  "success": false,
  "data": {
    "code": "ORDER_ITEM_REVIEWED",
    "message": "The order exists and the line item has been in the "reviewed" status",
    "path": "open-api/v1/orders/xx/cancel",
    "timestamp": 1785829541999
  }
}
```

---

## 3.4 修改店铺订单地址

### 基本信息

| 项目 | 内容 |
| --- | --- |
| URL | `/open-api/v1/orders/{orderId}/deliveryAddress` |
| 请求方式 | PUT |
| 接口语义 | 修改店铺订单收货地址 |
| 是否幂等 | 是 |
| 是否逻辑删除 | 否 |
| 是否版本升级 | 否 |
| 兼容性 | 向后兼容 |

### 接口校验规则

- 国家不可修改（沿用订单原地址的国家信息）；

### Header 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| Authorization | String | 是 | 店铺 Token |

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| orderId | Long | 是 | 店铺零售订单 id |

### Request Body

```json
{
    "stateCode": "CA",
    "state": "CA",
    "city": "Irvine",
    "address_1": "9906 Research Dr",
    "address_2": "",
    "postCode": "92602",
    "firstName": "Ziyer",
    "lastName": "Wong",
    "phone": "0777-123456",
    "mobile": "18475031246",
    "email": "ziyerwong@qpp.com"
}
```

### Response Body

响应结构与 3.3 一致，`deliveryAddress` 为修改后的地址。

### Error Response


---

# 4. Webhook 管理接口

Webhook 订阅字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | Long | Webhook id |
| name | String | Webhook 名称 |
| url | String | 回调地址 |
| eventTypes | List\<String\> | 订阅的事件类型列表 |
| enabled | Boolean | 是否启用 |

支持的事件类型：

| eventTypes 值 | 触发时机 | 推送数据 |
| --- | --- | --- |
| `order_item_received` | 店铺订单项状态变更为【received】 | 订单项数据（见 5.4.1） |
| `order_item_audited` | 店铺订单项状态变更为【reviewed】 | 订单项数据（见 5.4.1） |
| `order_item_produced` | 店铺订单项状态变更为【produced】 | 订单项数据（见 5.4.1） |
| `order_item_canceled` | 店铺订单项状态变更为【canceled】 | 订单项数据（见 5.4.1） |
| `order_item_failed` | 店铺订单项状态变更为【failed】 | 订单项数据（见 5.4.1） |
| `package_shipped` | 店铺订单发货时 | 发货数据（见 5.4.2） |

## 4.1 新增 Webhook
### 基本信息

| 项目 | 内容 |
| --- | --- |
| URL | `/open-api/v1/webhook` |
| 请求方式 | POST |
| 接口语义 | 新增 Webhook 订阅 |

### Header 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| Authorization | String | 是 | 店铺 Token |

### Request Body

```json
{
  "name": "order create event",
  "url": "https://mystore.example.com/webhook/order",
  "eventTypes": ["order_item_received", "order_item_audited"],
  "enabled": true
}
```

### 接口校验规则
- `url` 必填，且必须是合法的url格式，否则返回 `422`；
- `eventTypes` 必填，且只能包含下表支持的事件类型，否则返回 `422`；
- `enabled` 缺省按 `false` 处理。


### Response Body

```json
{
  "success": true,
  "data": {
    "id": 8874569,
    "name": "order create event",
    "url": "https://mystore.example.com/webhook/order",
    "eventTypes": ["order_item_received", "order_item_audited"],
    "enabled": true
  }
}
```

### Error Response

`eventTypes` 包含不支持的事件类型时：

```json
HTTP/1.1 422 Bad Request
{
  "success": false,
  "data": {
    "code": "EVENT_TYPE_ERROR",
    "message": "eventTypes contain unsupported event types: order_item_return",
    "path": "open-api/v1/webhook",
    "timestamp": 1785829541999
  }
}
```

`url` 不合法：
```json
HTTP/1.1 422 Bad Request
{
  "success": false,
  "data": {
    "code": "URL_ERROR",
    "message": "webhook url: 213 is illegal",
    "path": "open-api/v1/webhook",
    "timestamp": 1785829541999
  }
}
```

---

## 4.2 查询店铺所有 Webhook

### 基本信息

| 项目 | 内容 |
| --- | --- |
| URL | `/open-api/v1/webhook` |
| 请求方式 | GET |
| 接口语义 | 分页查询店铺所有 Webhook |
| 是否幂等 | 是 |
| 是否支持过滤 | 是（enabled / name / eventTypes） |
| 是否支持排序 | 否 |

### Header 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| Authorization | String | 是 | 店铺 Token |

### Query 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| page | int | 是 | 页码，从 1 开始 |
| size | int | 是 | 每页数量（<=0 按 25 处理） |
| enabled | Boolean | 否 | 按启用状态过滤 |
| name | String | 否 | 按名称过滤 |
| eventTypes | Set\<String\> | 否 | 按事件类型过滤，可重复传参，多个值取并集 |

### Response Body

```json
{
  "success": true,
  "data": {
    "pageNumber": 1,
    "pageSize": 20,
    "totalCount": 100,
    "totalPages": 5,
    "content": [
      {
        "id": 231323441,
        "name": "order create event",
        "url": "https://mystore.example.com/webhook/order",
        "eventTypes": ["order_item_received", "order_item_audited"],
        "enabled": true
      }
    ]
  }
}
```

### Error Response

---

## 4.3 查询店铺指定 Webhook

### 基本信息

| 项目 | 内容 |
| --- | --- |
| URL | `/open-api/v1/webhook/{id}` |
| 请求方式 | GET |
| 接口语义 | 查询店铺指定 id 的 Webhook |
| 是否幂等 | 是 |

### Header 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| Authorization | String | 是 | 店铺 Token |

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | Long | 是 | Webhook id |

### Response Body

```json
{
  "success": true,
  "data": {
    "id": 231323441,
    "name": "order create event",
    "url": "https://mystore.example.com/webhook/order",
    "eventTypes": ["order_item_received", "order_item_audited"],
    "enabled": true
  }
}
```

---

## 4.4 更新 Webhook

### 基本信息

| 项目 | 内容 |
| --- | --- |
| URL | `/open-api/v1/webhook/{id}` |
| 请求方式 | PUT |
| 接口语义 | 更新 Webhook 名称/回调地址/订阅事件/启用状态 |
| 是否幂等 | 是 |
| 是否逻辑删除 | 否 |

### Header 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| Authorization | String | 是 | 店铺 Token |

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | Long | 是 | Webhook id |

### Request Body

```json
{
  "name": "order create event",
  "url": "https://mystore.example.com/webhook/order",
  "eventTypes": ["order_item_received", "order_item_audited", "package_shipped"],
  "enabled": true
}
```

### 接口校验规则

- 可更新 `name`、`url`、`eventTypes`、`enabled`；
- `enabled` 用于启用/禁用 Webhook（`true` 启用，`false` 禁用）；
- `eventTypes` 只能包含支持的事件类型，否则返回 `422`；
- Webhook 不存在 `404`。

### Response Body

```json
{
  "success": true,
  "data": {
    "id": 231323441,
    "name": "order create event",
    "url": "https://mystore.example.com/webhook/order",
    "eventTypes": ["order_item_received", "order_item_audited", "package_shipped"],
    "enabled": true
  }
}
```

### Error Response
同webhook新增API错误响应

---

## 4.5 删除 Webhook

### 基本信息

| 项目 | 内容 |
| --- | --- |
| URL | `/open-api/v1/webhook/{id}` |
| 请求方式 | DELETE |
| 接口语义 | 删除 Webhook 订阅 |
| 是否逻辑删除 | 是 |

### Header 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| Authorization | String | 是 | 店铺 Token |

### Path 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | Long | 是 | Webhook id |

### Response Body

```json
{
  "success": true,
  "data": {
    "id": 231323441,
    "name": "order create event",
    "url": "https://mystore.example.com/webhook/order",
    "eventTypes": ["order_item_received", "order_item_audited"],
    "enabled": true
  }
}
```

---

# 5. Webhook 事件推送

## 5.1 概述

当业务事件发生（如店铺订单项状态变更、发货）时，系统向所有「已启用且订阅了该事件类型」的 Webhook 回调地址推送通知：

- HTTP 方法：`POST`；
- `Content-Type: application/json`；
- 每个订阅了该事件类型的 Webhook 都会收到一次推送；
- 请求头包含签名等 `x-qpmn-*` 头（见 5.3），接收方必须校验签名（见 5.5）；

## 5.2 事件类型

| eventTypes 值 | 触发时机 | 推送数据 |
| --- | --- | --- |
| `order_item_received` | 店铺订单项状态变更为【received】 | 订单项数据（见 5.4.1） |
| `order_item_audited` | 店铺订单项状态变更为【reviewed】 | 订单项数据（见 5.4.1） |
| `order_item_produced` | 店铺订单项状态变更为【produced】 | 订单项数据（见 5.4.1） |
| `order_item_canceled` | 店铺订单项状态变更为【canceled】 | 订单项数据（见 5.4.1） |
| `order_item_failed` | 店铺订单项状态变更为【failed】 | 订单项数据（见 5.4.1） |
| `package_shipped` | 店铺订单发货时 | 发货数据（见 5.4.2） |

## 5.3 推送请求头（Headers）

| Header | 类型 | 说明 |
| --- | --- | --- |
| `x-qpmn-webhook-id` | Long | 订阅该事件的 Webhook id |
| `x-qpmn-event-type` | String | 事件类型，取值见 5.2 |
| `x-qpmn-event-id` | String | 事件唯一标识，可用于幂等去重 |
| `x-qpmn-store-domain` | String | 店铺域名，可用于识别来源店铺 |
| `x-qpmn-hmac-sha256` | String | 请求体签名：`HMAC-SHA256(key = 店铺 Token, message = 请求体原始字节)` 的十六进制（hex，小写）字符串 |

完整推送请求示例（`order_item_received`）：

```http
POST {webhook_url} HTTP/1.1
Host: {webhook_url_host}
Content-Type: application/json
x-qpmn-webhook-id: 231323441
x-qpmn-event-type: order_item_received
x-qpmn-event-id: 8f7f35a3-4f5a-4b6e-9a2b-2e5c9d8a1b3c
x-qpmn-store-domain: https://mystore.example.com
x-qpmn-hmac-sha256: 3f2a8c1d7e0b4f6a9d2c5e8b1a3f7d0c9e2b4a6f8d1c3e5b7a9f0d2c4e6b8a0c

{请求体，见 5.4}
```

## 5.4 推送请求体示例（Payloads）

### 5.4.1 订单项事件（order_item_*）

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | String | 店铺零售订单项 id |
| externalId | String | 外部订单项 id |
| unitPrice | BigDecimal | 单价 |
| storeProductId | String | 店铺产品 id |
| quantity | int | 数量 |
| status | String | 订单项当前状态 |
| shipments | List | 发货信息，未发货时为空 |

```json
{
  "id": "1234567890",
  "externalId": "202502190042-1",
  "unitPrice": 10.00,
  "storeProductId": "266931519",
  "quantity": 1,
  "status": "received",
  "shipments": [
    {
      "id": 90001,
      "orderId": 321,
      "trackingNumber": "SF1234567890",
      "trackingUrl": "https://www.sf-express.com/waybill/SF1234567890",
      "company": "SF",
      "shipDate": 1750000000000,
      "items": [
        {
          "itemId": "1234567890",
          "quantity": 1
        }
      ]
  }
  ]
}
```

### 5.4.2 发货事件（package_shipped）

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | Long | 发货单 id |
| orderId | Long | 店铺零售订单 id |
| trackingNumber | String | 运单号 |
| trackingUrl | String | 运单查询 url |
| company | String | 发货物流公司 |
| shipDate | Date | 发货日期（时间戳毫秒，如 `1750000000000`） |
| items | List | 本次发货的订单项明细 |

`items` 明细字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| itemId | String | 店铺零售订单项 id |
| quantity | int | 发货数量 |

```json
{
  "id": 90001,
  "orderId": 321,
  "trackingNumber": "SF1234567890",
  "trackingUrl": "https://www.sf-express.com/waybill/SF1234567890",
  "company": "SF",
  "shipDate": 1750000000000,
  "items": [
    {
      "itemId": "1234567890",
      "quantity": 1
    }
  ]
}
```

> 注：空值字段是否出现在 JSON 中取决于序列化配置；`shipDate` 为时间戳毫秒格式。

## 5.5 签名校验（Verification）

### 校验步骤

1. 接收推送请求，保存**原始请求体字节**（不要重新格式化或重新序列化 JSON，否则签名不一致）；
2. 使用店铺 Token（与调用管理 API 时 `Authorization: Basic {store_token}` 中的 token 相同）作为 HMAC key；
3. 对原始请求体字节计算 `HMAC-SHA256`；
4. 将摘要转换为十六进制字符串（小写）；
5. 与请求头 `x-qpmn-hmac-sha256` 做**常量时间比较**（避免时序攻击）；
6. 可选：校验 `x-qpmn-store-domain` 与已配置的店铺域名一致；
7. 使用 `x-qpmn-event-id` 做幂等去重（同一 event id 只处理一次）。

### 签名算法说明

```text
signature = hex( HMAC-SHA256( key = store_token, message = raw_request_body ) )
```

### 校验示例（Java）

```java
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.apache.commons.codec.binary.Hex;

public boolean verify(String storeToken, byte[] rawBody, String signatureHeader) throws Exception {
    Mac mac = Mac.getInstance("HmacSHA256");
    mac.init(new SecretKeySpec(storeToken.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
    String expected = Hex.encodeHexString(mac.doFinal(rawBody));
    return MessageDigest.isEqual(
            expected.getBytes(StandardCharsets.UTF_8),
            signatureHeader.getBytes(StandardCharsets.UTF_8));
}
```

### 校验示例（Python）

```python
import hashlib
import hmac

def verify(store_token: str, raw_body: bytes, signature_header: str) -> bool:
    expected = hmac.new(
        store_token.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header.lower())
```

### 校验失败处理

- 签名缺失或不匹配：拒绝处理，建议返回 `401 Unauthorized`；
- 校验通过后按 `x-qpmn-event-type` 分发处理。

## 5.6 重试与可靠性

- 事件投递状态：待推送、推送中、已推送、失败、死信；
- 推送失败后自动重试，支持固定间隔或指数退避（初始延迟按倍数递增并封顶最大延迟），超过最大重试次数后进入死信；
- 重试间隔与次数以平台配置为准；
- 接收方应尽快返回 2xx（如 200），避免因处理超时被判定为失败而重复推送；
- 接收方应使用 `x-qpmn-event-id` 保证幂等。

---

# 6. 字段说明

## 6.1 地址

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| countryCode | String | 否 | 国家代号 |
| countryName | String | 否 | 国家名称 |
| stateCode | String | 否 | 地区/州代号 |
| state | String | 否 | 地区/州名称 |
| city | String | 是 | 城市 |
| address_1 | String | 否 | 地址第一行 |
| address_2 | String | 否 | 地址第二行 |
| postCode | String | 否 | 邮政编码 |
| firstName | String | 是 | 姓 |
| lastName | String | 是 | 名 |
| phone | String | 是 | 电话 |
| mobile | String | 否 | 移动电话 |
| email | String | 是 | 邮箱 |

> 注：序列化字段名为 `address_1`/`address_2`，非驼峰 `address1`/`address2`。

## 6.2 价格

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| currency | String | 币种 |
| subtotal | BigDecimal | 产品总价 |
| discount | BigDecimal | 折扣 |
| shipping | BigDecimal | 运费 |
| tax | BigDecimal | 税费 |

## 6.3 订单项（订单接口使用）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| externalId | String | 外部订单项 id |
| unitPrice | BigDecimal | 单价 |
| storeProductId | String | 店铺产品 id |
| quantity | int | 数量 |
| productDesignData | Object | 定制数据（创建订单时可传） |

定制数据（`productDesignData`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| designData | List | 物料定制数据，`code` 为物料路径Base64编码，`views[].code` 为定制面 code，`designs[].index` 为序号，`effectImages[]` 为不同工艺的定制图 |
| designAttributeValues | List | 设计属性，元素为 `code` + `value` |

## 6.4 Webhook 订阅

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | Long | Webhook id |
| name | String | Webhook 名称 |
| url | String | 回调地址 |
| eventTypes | List\<String\> | 订阅的事件类型列表 |
| enabled | Boolean | 是否启用 |

## 6.5 订单项事件推送数据

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | String | 店铺零售订单项 id |
| externalId | String | 外部订单项 id |
| unitPrice | BigDecimal | 单价 |
| storeProductId | String | 店铺产品 id |
| quantity | int | 数量 |
| status | String | 订单项当前状态 |
| shipments | List | 发货信息，未发货时为空 |

## 6.6 发货事件推送数据

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | Long | 发货单 id |
| orderId | Long | 店铺零售订单 id |
| trackingNumber | String | 运单号 |
| trackingUrl | String | 运单查询 url |
| company | String | 发货物流公司 |
| shipDate | Date | 发货日期（时间戳毫秒） |
| items | List | 发货订单项明细，元素为 `itemId` + `quantity` |

---
