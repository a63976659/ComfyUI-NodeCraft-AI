"""
API 文档模块 - OpenAPI 3.0 规范 + Swagger UI
"""
import copy
from aiohttp import web
from .系统环境映射 import 项目版本


# ─── Swagger UI HTML 页面 ─────────────────────────────────────

SWAGGER_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>NodeCraft AI - API 文档</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
    <div id="missing-endpoints-notice" style="padding: 10px 20px; background: #fff3cd; border-bottom: 1px solid #ffeaa7; display: none; font-size: 14px; color: #856404;"></div>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        SwaggerUIBundle({
            url: '/ai-coder/openapi.json',
            dom_id: '#swagger-ui',
            deepLinking: true,
            presets: [SwaggerUIBundle.presets.apis],
        });
    </script>
    <!--MISSING_NOTICE-->
</body>
</html>"""


# ─── OpenAPI 3.0 规范 ─────────────────────────────────────────

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "NodeCraft AI API",
        "description": "节点梦工厂 - ComfyUI AI 编程助手 API",
        "version": 项目版本,
        "contact": {"name": "NodeCraft AI Team"}
    },
    "servers": [{"url": "/ai-coder", "description": "主 API 服务"}],
    "tags": [
        {"name": "会话管理", "description": "对话会话的增删查"},
        {"name": "聊天", "description": "AI 对话（非流式 / SSE 流式 / WebSocket）"},
        {"name": "设置", "description": "系统设置读写"},
        {"name": "模型管理", "description": "本地模型扫描与管理"},
        {"name": "文件操作", "description": "插件文件读写与目录管理"},
        {"name": "GitHub 同步", "description": "插件代码同步到 GitHub"},
        {"name": "审计", "description": "操作审计日志查询"},
        {"name": "WebSocket", "description": "实时双向通信"},
    ],
    "components": {
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "JWT Token（单用户模式下可选）"
            }
        },
        "schemas": {
            "SuccessResponse": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "example": True},
                    "message": {"type": "string"}
                }
            },
            "ErrorResponse": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "example": False},
                    "message": {"type": "string"}
                }
            }
        }
    },
    "paths": {
        # ─── 会话管理 ───
        "/sessions": {
            "get": {
                "tags": ["会话管理"],
                "summary": "获取会话列表",
                "description": "返回所有会话的摘要列表（id、title、created_at）。",
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {"description": "成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"sessions": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "created_at": {"type": "string"}}}}}}}}},
                    "401": {"description": "未认证"},
                    "500": {"description": "服务器内部错误"}
                }
            },
            "post": {
                "tags": ["会话管理"],
                "summary": "创建会话",
                "description": "创建新的对话会话。标题长度不能超过500字符。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string", "default": "新会话", "description": "会话标题（最长500字符）"},
                                    "plugin_folder": {"type": "string", "default": "", "description": "关联的插件目录名"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "创建成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"session": {"type": "object", "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "created_at": {"type": "string"}, "plugin_folder": {"type": "string"}, "messages": {"type": "array"}}}}}}}},
                    "400": {"description": "标题过长"},
                    "401": {"description": "未认证"},
                    "429": {"description": "请求过于频繁"}
                }
            }
        },
        "/sessions/{id}": {
            "delete": {
                "tags": ["会话管理"],
                "summary": "删除会话",
                "description": "删除指定会话及其消息记录文件。",
                "security": [{"BearerAuth": []}],
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "会话 UUID"}],
                "responses": {
                    "200": {"description": "删除成功"},
                    "401": {"description": "未认证"},
                    "500": {"description": "服务器内部错误"}
                }
            }
        },
        "/sessions/{id}/messages": {
            "get": {
                "tags": ["会话管理"],
                "summary": "获取会话消息",
                "description": "获取指定会话的所有消息记录。",
                "security": [{"BearerAuth": []}],
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "会话 UUID"}],
                "responses": {
                    "200": {"description": "成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"messages": {"type": "array", "items": {"type": "object", "properties": {"role": {"type": "string", "enum": ["user", "assistant"]}, "content": {"type": "string"}, "timestamp": {"type": "string"}}}}}}}}},
                    "401": {"description": "未认证"},
                    "404": {"description": "会话不存在"}
                }
            }
        },

        # ─── 聊天 ───
        "/chat": {
            "post": {
                "tags": ["聊天"],
                "summary": "非流式对话",
                "description": "发送消息并等待完整回复。支持附件和插件上下文注入。内部流程：RAG 检索 → 构造提示词 → 压缩上下文 → 调用 LLM → 保存回复。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["session_id", "message"],
                                "properties": {
                                    "session_id": {"type": "string", "description": "会话 ID"},
                                    "message": {"type": "string", "description": "用户消息内容"},
                                    "attachments": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "type": {"type": "string"}, "size": {"type": "integer"}}}, "description": "附件列表"},
                                    "plugin_context": {"type": "string", "description": "插件目录名/路径，注入文件树上下文"},
                                    "model_source": {"type": "string", "enum": ["api", "local"], "description": "模型来源（覆盖设置）"},
                                    "local_model_name": {"type": "string", "description": "指定本地模型名称"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string", "example": "success"}, "reply": {"type": "string", "description": "AI 回复内容"}}}}}},
                    "400": {"description": "缺少 session_id 或模型未配置"},
                    "401": {"description": "未认证"},
                    "404": {"description": "会话不存在"},
                    "429": {"description": "请求过于频繁"},
                    "503": {"description": "智能体模块未加载"}
                }
            }
        },
        "/chat-stream": {
            "post": {
                "tags": ["聊天"],
                "summary": "SSE 流式对话",
                "description": "发送消息并以 Server-Sent Events 流式返回回复。每个 chunk 为 JSON：{content, done}。最后一条 done=true 表示结束。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["session_id", "message"],
                                "properties": {
                                    "session_id": {"type": "string"},
                                    "message": {"type": "string"},
                                    "attachments": {"type": "array", "items": {"type": "object"}},
                                    "plugin_context": {"type": "string"},
                                    "model_source": {"type": "string", "enum": ["api", "local"]},
                                    "local_model_name": {"type": "string"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "SSE 事件流", "content": {"text/event-stream": {"schema": {"type": "string", "description": "data: {\"content\": \"...\", \"done\": false}\\n\\n"}}}},
                    "400": {"description": "参数错误"},
                    "401": {"description": "未认证"},
                    "429": {"description": "请求过于频繁"}
                }
            }
        },

        # ─── 设置 ───
        "/settings": {
            "get": {
                "tags": ["设置"],
                "summary": "获取设置",
                "description": "获取当前系统设置，包含 default_local_path（本地模型默认路径）。",
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {"description": "成功，返回设置对象", "content": {"application/json": {"schema": {"type": "object", "properties": {"model_source": {"type": "string"}, "api_key": {"type": "string"}, "api_url": {"type": "string"}, "model_name": {"type": "string"}, "local_model_name": {"type": "string"}, "local_path": {"type": "string"}, "default_local_path": {"type": "string"}, "max_tokens": {"type": "integer"}, "github_token": {"type": "string"}, "github_username": {"type": "string"}, "github_visibility": {"type": "string"}}}}}},
                    "401": {"description": "未认证"}
                }
            },
            "post": {
                "tags": ["设置"],
                "summary": "保存设置",
                "description": "保存系统设置。default_local_path 字段会被自动忽略（只读字段）。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "description": "设置对象（键值对自由格式）"
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "保存成功"},
                    "401": {"description": "未认证"},
                    "429": {"description": "请求过于频繁"}
                }
            }
        },

        # ─── 模型管理 ───
        "/local-models": {
            "get": {
                "tags": ["模型管理"],
                "summary": "获取本地模型列表",
                "description": "扫描本地 LLM 目录（models/LLM），返回可用模型列表。支持 transformers 和 GGUF 格式。",
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {"description": "成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"models": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": ["transformers", "gguf"]}, "path": {"type": "string"}}}}}}}}}
                }
            }
        },
        "/unload-model": {
            "post": {
                "tags": ["模型管理"],
                "summary": "卸载本地模型",
                "description": "卸载当前加载的本地模型，释放显存/内存。",
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {"description": "卸载成功"},
                    "429": {"description": "请求过于频繁"},
                    "500": {"description": "模型客户端未初始化"}
                }
            }
        },
        "/reset-model-status": {
            "post": {
                "tags": ["模型管理"],
                "summary": "重置模型状态",
                "description": "清除模型失败黑名单，允许重新加载之前失败的模型。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "model_name": {"type": "string", "nullable": True, "description": "指定模型名称，null 则重置所有"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "重置成功"},
                    "429": {"description": "请求过于频繁"}
                }
            }
        },

        # ─── 文件操作 ───
        "/create-folder": {
            "post": {
                "tags": ["文件操作"],
                "summary": "创建插件脚手架",
                "description": "在 custom_nodes 目录下创建插件脚手架目录结构。插件名不能超过128字符，不能使用 Windows 保留字。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["plugin_name"],
                                "properties": {
                                    "plugin_name": {"type": "string", "description": "插件名称（最长128字符）"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "创建结果", "content": {"application/json": {"schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "message": {"type": "string"}, "path": {"type": "string"}}}}}},
                    "400": {"description": "名称不合法"},
                    "429": {"description": "请求过于频繁"}
                }
            }
        },
        "/local-plugins": {
            "get": {
                "tags": ["文件操作"],
                "summary": "获取本地插件列表",
                "description": "扫描 custom_nodes 目录，返回所有插件目录名列表。",
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {"description": "成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"plugins": {"type": "array", "items": {"type": "string"}}}}}}}
                }
            }
        },
        "/browse-folder": {
            "post": {
                "tags": ["文件操作"],
                "summary": "浏览文件夹",
                "description": "浏览指定目录下的子文件夹列表。路径必须在 custom_nodes 范围内（防止路径穿越）。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "initial_dir": {"type": "string", "description": "目录路径（默认为 custom_nodes）"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}, "current_dir": {"type": "string"}, "folders": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "path": {"type": "string"}}}}}}}}},
                    "400": {"description": "路径不存在或非目录"},
                    "403": {"description": "路径超出允许范围"},
                    "429": {"description": "请求过于频繁"}
                }
            }
        },
        "/analyze-plugin": {
            "post": {
                "tags": ["文件操作"],
                "summary": "分析插件依赖",
                "description": "分析插件的依赖关系，返回可视化 graph 数据。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["plugin_path"],
                                "properties": {
                                    "plugin_path": {"type": "string", "description": "插件路径（相对或绝对）"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "分析结果", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}, "data": {"type": "object"}}}}}},
                    "400": {"description": "缺少参数"},
                    "404": {"description": "插件路径不存在"}
                }
            }
        },
        "/plugin-files": {
            "post": {
                "tags": ["文件操作"],
                "summary": "获取插件文件树",
                "description": "获取指定插件的完整文件树结构。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["plugin_path"],
                                "properties": {
                                    "plugin_path": {"type": "string", "description": "插件路径"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "tree": {"type": "array"}, "plugin_path": {"type": "string"}}}}}},
                    "400": {"description": "缺少参数"},
                    "404": {"description": "路径不存在"}
                }
            }
        },
        "/read-file": {
            "post": {
                "tags": ["文件操作"],
                "summary": "读取文件",
                "description": "读取插件内指定文件的内容。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["plugin_path", "file_path"],
                                "properties": {
                                    "plugin_path": {"type": "string", "description": "插件路径"},
                                    "file_path": {"type": "string", "description": "相对于插件根目录的文件路径"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "content": {"type": "string"}}}}}},
                    "400": {"description": "参数缺失或文件读取失败"}
                }
            }
        },
        "/write-file": {
            "post": {
                "tags": ["文件操作"],
                "summary": "写入文件",
                "description": "写入或修改插件内的文件（自动备份原文件）。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["plugin_path", "file_path", "content"],
                                "properties": {
                                    "plugin_path": {"type": "string", "description": "插件路径"},
                                    "file_path": {"type": "string", "description": "相对于插件根目录的文件路径"},
                                    "content": {"type": "string", "description": "文件内容"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "写入成功"},
                    "400": {"description": "参数缺失或写入失败"}
                }
            }
        },

        # ─── GitHub 同步 ───
        "/github-sync": {
            "post": {
                "tags": ["GitHub 同步"],
                "summary": "执行 GitHub 同步",
                "description": "将插件代码同步（上传）到 GitHub 仓库。需要在设置中配置 github_token 和 github_username。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["plugin_path", "repo_name"],
                                "properties": {
                                    "plugin_path": {"type": "string", "description": "插件路径"},
                                    "repo_name": {"type": "string", "description": "GitHub 仓库名"},
                                    "ignore_gitignore": {"type": "boolean", "default": False, "description": "是否忽略 .gitignore 规则"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "同步成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}, "repo_url": {"type": "string"}, "uploaded_count": {"type": "integer"}}}}}},
                    "400": {"description": "参数错误或仓库名不合法"},
                    "404": {"description": "插件路径不存在"},
                    "429": {"description": "请求过于频繁"}
                }
            }
        },
        "/github-check-repo": {
            "post": {
                "tags": ["GitHub 同步"],
                "summary": "检查仓库是否存在",
                "description": "检查指定 GitHub 仓库是否已存在。",
                "security": [{"BearerAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["repo_name"],
                                "properties": {
                                    "repo_name": {"type": "string", "description": "仓库名称"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "检查结果", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}, "exists": {"type": "boolean"}, "repo_name": {"type": "string"}}}}}},
                    "400": {"description": "参数缺失或 GitHub 未配置"},
                    "429": {"description": "请求过于频繁"}
                }
            }
        },
        "/github-test-connection": {
            "post": {
                "tags": ["GitHub 同步"],
                "summary": "测试 GitHub 连接",
                "description": "测试 GitHub Token 和用户名的连接有效性。",
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {"description": "测试结果", "content": {"application/json": {"schema": {"type": "object", "properties": {"valid": {"type": "boolean"}, "message": {"type": "string"}}}}}},
                    "429": {"description": "请求过于频繁"}
                }
            }
        },

        # ─── 审计 ───
        "/audit-logs": {
            "get": {
                "tags": ["审计"],
                "summary": "查询审计日志",
                "description": "查询当前用户的操作审计日志，默认返回最近50条。",
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {"description": "成功", "content": {"application/json": {"schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "data": {"type": "array", "items": {"type": "object"}}}}}}},
                    "401": {"description": "未认证"}
                }
            }
        },

        # ─── WebSocket ───
        "/ws": {
            "get": {
                "tags": ["WebSocket"],
                "summary": "WebSocket 连接",
                "description": "建立 WebSocket 实时通信连接。首条消息必须是认证消息 {type: 'auth', token: '...'}。支持消息类型：auth（认证）、chat（对话）、ping（心跳）。响应类型：auth_ok、auth_error、chunk、done、error、pong。",
                "responses": {
                    "101": {"description": "WebSocket 升级成功"}
                }
            }
        }
    }
}


# ─── 路由元数据自动提取 ───────────────────────────────────

def _extract_routes_metadata(app) -> dict:
    """从 aiohttp app 路由表自动提取路由元数据。

    遍历 app.router.routes()，提取每个路由的 method、path、handler 名称和 docstring。
    仅提取 /ai-coder/* 前缀的路由，跳过 /v1/ai-coder/* 别名和文档端点自身。

    Returns:
        dict: {path: {method: {"summary": str, "description": str}}}
        其中 path 已剥离 /ai-coder 前缀，与 OPENAPI_SPEC 的 paths 格式一致。
    """
    result = {}
    skip_prefixes = ('/ai-coder/docs', '/ai-coder/openapi.json')

    for route in app.router.routes():
        try:
            method = route.method
            # aiohttp 中 '*' 表示所有方法，跳过
            if method == '*':
                continue
            method = method.lower()

            resource = route.resource
            if resource is None:
                continue
            path = resource.canonical

            # 仅保留 /ai-coder/* 路由
            if not path.startswith('/ai-coder/'):
                continue
            # 跳过 /v1/ 前缀别名
            if path.startswith('/v1/'):
                continue
            # 跳过文档端点自身
            if any(path.startswith(skip) for skip in skip_prefixes):
                continue

            # 剥离 /ai-coder 前缀，与 OPENAPI_SPEC 的 paths 格式对齐
            relative_path = path[len('/ai-coder'):]

            handler = route.handler
            handler_name = getattr(handler, '__name__', str(handler))
            docstring = (getattr(handler, '__doc__', '') or '').strip()

            if relative_path not in result:
                result[relative_path] = {}

            if method not in result[relative_path]:
                result[relative_path][method] = {
                    "summary": handler_name,
                    "description": docstring or f"Handler: {handler_name}",
                }
        except Exception:
            continue

    return result


def _find_missing_endpoints(manual_paths: set, extracted_routes: dict) -> list:
    """找出手动 spec 中缺失但实际存在的端点。

    Args:
        manual_paths: 手动 spec 中的路径集合（如 {"/sessions", "/chat", ...}）
        extracted_routes: _extract_routes_metadata 返回的路由字典

    Returns:
        list: 缺失端点的列表，每项为 {"path": str, "methods": [str, ...]}
    """
    missing = []
    for path, methods in extracted_routes.items():
        if path not in manual_paths:
            missing.append({
                "path": path,
                "methods": list(methods.keys()),
            })
    return missing


# ─── 路由处理函数 ─────────────────────────────────────────────

async def docs_page(request):
    """Swagger UI 文档页面"""
    extracted = _extract_routes_metadata(request.app)
    manual_paths = set(OPENAPI_SPEC.get("paths", {}).keys())
    missing = _find_missing_endpoints(manual_paths, extracted)
    missing_count = len(missing)

    html = SWAGGER_HTML
    if missing_count > 0:
        notice_text = f"⚠️ 自动检测到 {missing_count} 个未文档化的端点（详见 OpenAPI JSON 中的 paths）"
        notice_script = f"""
    <script>
        document.addEventListener('DOMContentLoaded', function() {{
            var notice = document.getElementById('missing-endpoints-notice');
            notice.textContent = '{notice_text}';
            notice.style.display = 'block';
        }});
    </script>"""
        html = html.replace('<!--MISSING_NOTICE-->', notice_script)
    return web.Response(text=html, content_type='text/html')


async def openapi_json(request):
    """OpenAPI JSON 规范"""
    spec = copy.deepcopy(OPENAPI_SPEC)

    # 自动提取路由并合并缺失端点
    extracted = _extract_routes_metadata(request.app)
    manual_paths = set(spec.get("paths", {}).keys())
    missing = _find_missing_endpoints(manual_paths, extracted)

    # 将缺失的端点补充到 spec 中（手动 spec 优先，不覆盖已有描述）
    for item in missing:
        path = item["path"]
        path_item = {}
        for method in item["methods"]:
            meta = extracted[path][method]
            path_item[method] = {
                "tags": ["自动检测"],
                "summary": meta["summary"],
                "description": meta["description"],
                "security": [{"BearerAuth": []}],
                "responses": {
                    "200": {"description": "成功"}
                },
            }
        spec["paths"][path] = path_item

    return web.json_response(spec)
