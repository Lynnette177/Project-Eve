已完成事件记忆层和 `memory_agent.save_memory` 落库逻辑。

**新增事件记忆表**
- `/Users/lynnette/Documents/Project Eve/Project Eve/database.py:94`
- 表名：`event_memories`
- 字段包括：
  - `content`：记忆内容
  - `category`：类别
  - `happened_at`：发生时间
  - `valid_until`：有效期
  - `status`：`active / expired / archived / forgotten / deleted`
  - `importance`：重要性权重
  - `emotional_weight`：情绪权重
  - `recall_count`：被回忆次数
  - `mention_count`：被重复提到次数
  - `forget_weight`：遗忘权重
  - `source_message_ids`：来源消息
  - `last_recalled_at / created_at / updated_at`

**新增数据库接口**
- `/Users/lynnette/Documents/Project Eve/Project Eve/database.py:538`
- 主要函数：
  - `add_event_memory(...)`
  - `get_event_memory(memory_id)`
  - `list_event_memories(...)`
  - `search_event_memories(query, ...)`
  - `update_event_memory(...)`
  - `reinforce_event_memory(...)`
  - `save_event_memory_dedup(...)`
  - `mark_event_memory_recalled(...)`
  - `decay_event_memories(...)`

**去重与加权**
- `save_event_memory_dedup(...)` 会先搜索相似记忆。
- 如果重复：
  - 不新增
  - `mention_count + 1`
  - `recall_count + 1`
  - `importance / emotional_weight / forget_weight` 增加
- 如果不重复：
  - 创建新事件记忆

**每日遗忘函数**
你每天可以调用：

```python
evedb.decay_event_memories()
```

默认行为：
```text
forget_weight 逐渐降低
active → expired
expired → archived
archived → forgotten
forgotten → deleted
```

也可以自定义：

```python
evedb.decay_event_memories(
    decay=0.15,
    expire_below=0.8,
    archive_below=0.35,
    forget_below=0.12,
    delete_below=0.03,
)
```

**Memory Agent 已接入**
- `/Users/lynnette/Documents/Project Eve/Project Eve/ai/agents/memory_agent.py:42`
- `save_memory` 现在会：
  - 读取 `summary/category/importance/emotional_weight/happened_at/valid_until`
  - 调用 `evedb.save_event_memory_dedup(...)`
  - 重复记忆加权，不重复新增

**工具 Schema 已扩展**
- `/Users/lynnette/Documents/Project Eve/Project Eve/ai/tool_schemas.py:19`
- `save_memory` 现在支持：
  - `summary`
  - `category`
  - `importance`
  - `emotional_weight`
  - `happened_at`
  - `valid_until`

**验证**
- 已通过语法检查。
- 已用临时数据库测试：
  - 新建记忆
  - 重复记忆加权
  - 搜索记忆
  - 执行遗忘衰减

现在你每天定时调用 `evedb.decay_event_memories()` 就能模拟遗忘。

# Core Memory
Core Memory 是 Project Eve 的**核心长期记忆系统**，用于保存那些不应该随着时间轻易遗忘、并且几乎每轮对话都可能影响回复风格的信息。

它和事件记忆不同：

```text
Event Memory：
记录“发生过什么”
例如：用户明天有面试、今天想点外卖、家里的狗今天不吃饭。

Core Memory：
记录“用户是什么样的人，以及希望 Eve 怎么陪伴他”
例如：用户喜欢短句回复、压力大时不喜欢被讲道理、喜欢被叫宝宝、讨厌命令式语气。
```

Core Memory 会被默认注入给 ChatAgent，作为长期用户画像和回复偏好，让 Eve 在每次聊天时都能保持一致的陪伴风格。

它适合保存：

```text
用户长期偏好
用户回复风格偏好
用户安慰方式偏好
用户称呼偏好
用户雷区和边界
用户稳定的情绪模式
用户和 Eve 的关系风格
```

Core Memory 使用 `memory_key` 作为稳定唯一标识，避免重复保存相似内容。例如：

```text
reply_style
comfort_style
nickname_preference
relationship_tone
boundary
user_preferences
```

当 MemoryAgent 发现新信息时，会判断它应该：

```text
新增 Core Memory
更新已有 Core Memory
强化已有 Core Memory
忽略无价值内容
```

例如：

```text
已有：
用户喜欢短句、自然、像微信聊天一样的回复。

用户又说：
你以后别给我发长篇，我喜欢你短短地哄我。

系统不会新增一条重复记忆，而是更新原有 reply_style：
用户喜欢短句、自然、像微信聊天一样的回复；不喜欢长篇回复，喜欢 Eve 短短地哄他。
```

因此，Core Memory 的作用是让 Eve 不只是“记得事件”，而是逐渐形成对用户的长期理解：

```text
怎么说话
怎么安慰
怎么称呼
哪些话不能说
用户喜欢什么关系氛围
用户希望 Eve 成为什么样的陪伴者
```

一句话总结：

> **Core Memory 是 Eve 的长期用户画像和相处规则库，负责让 Eve 越来越懂用户，并在每次回复中稳定体现这种理解。**