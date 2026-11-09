# Agent 架构审计

```json
{
  "schema_version": "ecc.agent-architecture-audit.report.v1",
  "executive_verdict": {
    "overall_health": "medium_risk",
    "primary_failure_mode": "Agent 运行时与配置可能不一致，修正循环可观测性不足",
    "most_urgent_fix": "显式选择运行时、限制循环并保存状态"
  },
  "scope": {
    "target_name": "Query Pilot Data Query Agent",
    "model_stack": ["DeepSeek OpenAI-compatible API", "LangGraph"],
    "layers_to_audit": ["tool selection", "tool execution", "hidden repair loops", "persistence"]
  },
  "findings": [
    {
      "severity": "high",
      "title": "LangGraph 缺失时静默回退",
      "mechanism": "配置声明与实际运行引擎不一致",
      "source_layer": "tool execution",
      "root_cause": "运行时工厂将依赖缺失视为普通降级",
      "evidence_refs": ["app/agent/runtime.py"],
      "confidence": 1.0,
      "recommended_fix": "显式配置 langgraph 时依赖缺失直接失败"
    },
    {
      "severity": "medium",
      "title": "SQL 自动修正循环可能不可见",
      "mechanism": "用户只能看到最终结果，难以判断模型重试次数",
      "source_layer": "hidden repair loops",
      "root_cause": "执行状态此前未持久化",
      "evidence_refs": ["app/agent/graph.py", "app/agent/checkpoint.py"],
      "confidence": 0.95,
      "recommended_fix": "限制修正次数并按 trace/thread 保存 checkpoint"
    }
  ],
  "ordered_fix_plan": [
    {"order": 1, "goal": "运行时显式失败", "why_now": "防止生产配置漂移", "expected_effect": "实际引擎可验证"},
    {"order": 2, "goal": "循环状态持久化", "why_now": "支持排障和恢复", "expected_effect": "每次修正可追踪"},
    {"order": 3, "goal": "增加人工审批恢复", "why_now": "高风险查询仍需人控", "expected_effect": "形成完整 HITL 链路"}
  ]
}
```
