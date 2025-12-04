

db.sessions.aggregate([
  {
    $addFields: {
      agent_state_length: { $size: { $ifNull: ["$agent_states", []] } }
    }
  },
  {
    $sort: { 
      tenant_id: 1,              // 按租户分组（升序）
      agent_state_length: -1,    // 组内按 agent_state_length 倒序
      creation_utc: -1           // 再按创建时间倒序
    }
  },
  {
    $project: {
      chatbot_id: 1,
      tenant_id: 1,
      creation_utc: 1,
      // updated_utc: 1,
      agent_state_length: 1,
    }
  }
])

