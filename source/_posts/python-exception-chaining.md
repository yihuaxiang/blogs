---
title: Python 异常别只打印字符串：异常链、边界转换与可诊断日志
date: 2026-11-04 03:10:37
tags:
  - Python
  - 异常处理
  - 可观测性
  - 工程实践
categories:
  - 工程实践
---

![Python 异常处理与诊断流程](/images/python-exception-chaining/cover.jpeg)

线上报错最怕两种极端：底层堆栈原样返回给用户，泄露实现细节；或者所有异常都被捕获后只打印一句“失败”，值班人员无法判断是输入错误、依赖故障还是程序缺陷。Python 的异常链和分层边界，能把原始原因保留下来，同时把对外契约和内部诊断分开。

<!-- more -->

## 先区分三类责任

异常处理可以按责任分三层。领域层负责表达业务规则，例如余额不足；适配层负责把数据库、HTTP 或文件库的异常翻译成稳定的应用异常；入口层负责选择状态码、用户提示和日志级别。每层只处理自己能恢复或解释的错误，未知异常继续向上冒泡，避免在深处悄悄吞掉故障。

```python
class AppError(Exception):
    """可安全映射为接口响应的基类。"""

class InvalidInput(AppError):
    pass

class DependencyUnavailable(AppError):
    pass
```

异常类型应表达调用方能采取的动作，而不是复制底层库的类名。`InvalidInput` 可以返回 400 并提示修改请求；`DependencyUnavailable` 可能返回 503 并允许稍后重试。数据库驱动的 `OperationalError` 不应直接成为公共 API 的长期契约。

## 用 `raise ... from ...` 保留因果

翻译异常时不要丢掉原始原因：

```python
import json


def load_profile(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInput("profile must be valid JSON") from exc
```

这里的 `__cause__` 让日志和调试器仍能看到 JSON 解析的行列号；对外只暴露稳定消息。`raise ... from None` 可以在确实不希望显示内部实现时隐藏链，但应谨慎使用：它适合边界展示，不适合把排障证据一起抹掉。

![原始异常沿调用链转换为稳定错误](/images/python-exception-chaining/chain.jpeg)

## 捕获范围要尽量窄

`except Exception` 不是通用修复方案。它会捕获拼写错误、断言失败和第三方库异常，若随后返回默认值，系统可能在错误状态下继续运行。优先捕获能处理的具体类型，并让取消、退出等控制流异常保持原语义：

```python
try:
    result = client.fetch(item_id)
except TimeoutError as exc:
    raise DependencyUnavailable("profile service timed out") from exc
```

如果需要统一记录未知异常，在最外层记录一次完整堆栈后重新抛出或交给框架生成 500。不要在每个函数都 `logger.exception`，否则同一次失败会产生多份重复日志，真正的根因反而被淹没。

## 边界转换要有明确策略

入口层可以把异常映射为机器可读的错误结构：

| 异常类别 | HTTP 状态 | 客户端动作 | 日志级别 |
| --- | ---: | --- | --- |
| `InvalidInput` | 400 | 修改请求 | info 或 warn |
| 未认证或无权限 | 401/403 | 登录或申请权限 | info |
| `DependencyUnavailable` | 503 | 退避重试 | warn |
| 未知异常 | 500 | 提交请求 ID | error |

响应里给出稳定的 `code`、简短 `message` 和 `request_id`，不要把 traceback、SQL、文件路径或密钥放进正文。日志记录异常类型、链路 ID、关键资源标识和堆栈；用户输入需按字段脱敏，避免“为了排查”制造新的隐私泄露。

![异常边界把用户响应与内部诊断分开](/images/python-exception-chaining/boundary.jpeg)

## 重试与清理不能混在一起

捕获异常后是否重试，要看操作是否可重复以及错误是否暂时。超时的只读请求通常可以有限重试；已经提交的写操作则需要幂等键或状态查询。清理逻辑放在 `finally` 或上下文管理器中，并保证清理失败不会覆盖原始异常：

```python
resource = acquire()
try:
    return process(resource)
finally:
    resource.close()
```

上线前用测试覆盖异常链、未知异常、重复日志、超时重试和清理失败。检查监控是否能按 `code` 聚合，日志是否能用 `request_id` 串起入口与依赖调用。好的异常处理不是让错误消失，而是让每个错误都在正确的边界被解释、记录并交给合适的恢复动作。
