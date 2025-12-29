// 66f141cf282e353aed7aa062
/**
 * 在 db.events 表中，找出 租户 66f141cf282e353aed7aa062 的所有AI发送code前缀的消息记录，以及对应的客户发送的消息记录：
 * 比如： 
 * ho000001 的记录 转人工
 * un000001 的记录 转兜底
 * 
 * 客户：转人工
 * AI：ho000001: 很高兴问你转接xxx客服，请稍等
 * 
 */

const tenantId = "66f141cf282e353aed7aa062";
const oneMonthAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);

// ============================================
// 查询 ho000001 开头的消息（转人工）
// 从 sessions 表通过 tenant_id 关联 events 表
// ============================================
db.sessions.aggregate([
  {
    $match: {
      tenant_id: tenantId,
      creation_utc: { $gte: oneMonthAgo.toISOString() }
    }
  },
  {
    $lookup: {
      from: "events",
      let: { sessionId: "$id" },
      pipeline: [
        {
          $match: {
            $expr: { $eq: ["$session_id", "$$sessionId"] },
            kind: "message",
            source: "ai_agent",
            "data.message": { $regex: /^ho000001:/ }
          }
        }
      ],
      as: "ai_messages"
    }
  },
  { $unwind: "$ai_messages" },
  {
    $lookup: {
      from: "events",
      let: { 
        sessionId: "$id", 
        aiTime: "$ai_messages.creation_utc" 
      },
      pipeline: [
        {
          $match: {
            $expr: {
              $and: [
                { $eq: ["$session_id", "$$sessionId"] },
                { $eq: ["$source", "customer"] },
                { $eq: ["$kind", "message"] },
                { $lt: ["$creation_utc", "$$aiTime"] }
              ]
            }
          }
        },
        { $sort: { creation_utc: -1 } },
        { $limit: 1 }
      ],
      as: "customer_message"
    }
  },
  {
    $unwind: {
      path: "$customer_message",
      preserveNullAndEmptyArrays: true
    }
  },
  {
    $project: {
      _id: 0,
      customer_message: "$customer_message.data.message",
      customer_message_time: { $substr: ["$customer_message.creation_utc", 0, 19] },
      ai_message: "$ai_messages.data.message",
      ai_message_time: { $substr: ["$ai_messages.creation_utc", 0, 19] },
      type: { $literal: "转人工(ho000001)" },
      tenant_id: "$tenant_id",
      chatbot_id: "$agent_id",
      session_id: "$id"
    }
  },
  { $sort: { ai_message_time: -1 } }
]);

// ============================================
// 查询 un000001 开头的消息（转兜底）
// 从 sessions 表通过 tenant_id 关联 events 表
// ============================================
db.sessions.aggregate([
  {
    $match: {
      tenant_id: tenantId,
      creation_utc: { $gte: oneMonthAgo.toISOString() }
    }
  },
  {
    $lookup: {
      from: "events",
      let: { sessionId: "$id" },
      pipeline: [
        {
          $match: {
            $expr: { $eq: ["$session_id", "$$sessionId"] },
            kind: "message",
            source: "ai_agent",
            "data.message": { $regex: /^un000001:/ }
          }
        }
      ],
      as: "ai_messages"
    }
  },
  { $unwind: "$ai_messages" },
  {
    $lookup: {
      from: "events",
      let: { 
        sessionId: "$id", 
        aiTime: "$ai_messages.creation_utc" 
      },
      pipeline: [
        {
          $match: {
            $expr: {
              $and: [
                { $eq: ["$session_id", "$$sessionId"] },
                { $eq: ["$source", "customer"] },
                { $eq: ["$kind", "message"] },
                { $lt: ["$creation_utc", "$$aiTime"] }
              ]
            }
          }
        },
        { $sort: { creation_utc: -1 } },
        { $limit: 1 }
      ],
      as: "customer_message"
    }
  },
  {
    $unwind: {
      path: "$customer_message",
      preserveNullAndEmptyArrays: true
    }
  },
  {
    $project: {
      _id: 0,
      customer_message: "$customer_message.data.message",
      customer_message_time: { $substr: ["$customer_message.creation_utc", 0, 19] },
      ai_message: "$ai_messages.data.message",
      ai_message_time: { $substr: ["$ai_messages.creation_utc", 0, 19] },
      type: { $literal: "转兜底(un000001)" },
      tenant_id: "$tenant_id",
      chatbot_id: "$agent_id",
      session_id: "$id"
    }
  },
  { $sort: { ai_message_time: -1 } }
]);

// ============================================
// 合并查询：同时获取 ho000001 和 un000001（互斥分类）
// 从 sessions 表通过 tenant_id 关联 events 表
// ============================================
db.sessions.aggregate([
  {
    $match: {
      tenant_id: tenantId,
      creation_utc: { $gte: oneMonthAgo.toISOString() }
    }
  },
  {
    $lookup: {
      from: "events",
      let: { sessionId: "$id" },
      pipeline: [
        {
          $match: {
            $expr: { $eq: ["$session_id", "$$sessionId"] },
            kind: "message",
            source: "ai_agent",
            $or: [
              { "data.message": { $regex: /^ho000001:/ } },
              { "data.message": { $regex: /^un000001:/ } }
            ]
          }
        }
      ],
      as: "ai_messages"
    }
  },
  { $unwind: "$ai_messages" },
  {
    $addFields: {
      code_type: {
        $cond: {
          if: { $regexMatch: { input: "$ai_messages.data.message", regex: /^ho000001:/ } },
          then: "转人工(ho000001)",
          else: "转兜底(un000001)"
        }
      }
    }
  },
  {
    $lookup: {
      from: "events",
      let: { 
        sessionId: "$id", 
        aiTime: "$ai_messages.creation_utc" 
      },
      pipeline: [
        {
          $match: {
            $expr: {
              $and: [
                { $eq: ["$session_id", "$$sessionId"] },
                { $eq: ["$source", "customer"] },
                { $eq: ["$kind", "message"] },
                { $lt: ["$creation_utc", "$$aiTime"] }
              ]
            }
          }
        },
        { $sort: { creation_utc: -1 } },
        { $limit: 1 }
      ],
      as: "customer_message"
    }
  },
  {
    $unwind: {
      path: "$customer_message",
      preserveNullAndEmptyArrays: true
    }
  },
  {
    $project: {
      _id: 0,
      customer_message: "$customer_message.data.message",
      customer_message_time: { $substr: ["$customer_message.creation_utc", 0, 19] },
      ai_message: "$ai_messages.data.message",
      ai_message_time: { $substr: ["$ai_messages.creation_utc", 0, 19] },
      code_type: 1,
      tenant_id: "$tenant_id",
      chatbot_id: "$agent_id",
      session_id: "$id"
    }
  },
  { $sort: { code_type: 1, ai_message_time: -1 } }
]);



// ============================================
// 统计汇总：分别统计 ho000001 和 un000001 的数量
// 从 sessions 表通过 tenant_id 关联 events 表
// ============================================
db.sessions.aggregate([
  {
    $match: {
      tenant_id: tenantId,
      creation_utc: { $gte: oneMonthAgo.toISOString() }
    }
  },
  {
    $lookup: {
      from: "events",
      let: { sessionId: "$id" },
      pipeline: [
        {
          $match: {
            $expr: { $eq: ["$session_id", "$$sessionId"] },
            kind: "message",
            source: "ai_agent",
            $or: [
              { "data.message": { $regex: /^ho000001:/ } },
              { "data.message": { $regex: /^un000001:/ } }
            ]
          }
        }
      ],
      as: "ai_messages"
    }
  },
  { $unwind: "$ai_messages" },
  {
    $addFields: {
      code_type: {
        $cond: {
          if: { $regexMatch: { input: "$ai_messages.data.message", regex: /^ho000001:/ } },
          then: "转人工(ho000001)",
          else: "转兜底(un000001)"
        }
      }
    }
  },
  {
    $group: {
      _id: {
        code_type: "$code_type",
        tenant_id: "$tenant_id",
        chatbot_id: "$agent_id"
      },
      count: { $sum: 1 },
      sessions: { $addToSet: "$id" }
    }
  },
  {
    $project: {
      _id: 0,
      code_type: "$_id.code_type",
      tenant_id: "$_id.tenant_id",
      chatbot_id: "$_id.chatbot_id",
      message_count: "$count",
      unique_session_count: { $size: "$sessions" }
    }
  }
]);




db.sessions.aggregate([
  {
    $match: {
      tenant_id: tenantId,
      creation_utc: { $gte: oneMonthAgo.toISOString() }
    }
  },
  {
    $lookup: {
      from: "events",
      let: { sessionId: "$id" },
      pipeline: [
        {
          $match: {
            $expr: { $eq: ["$session_id", "$$sessionId"] },
            kind: "message",
            source: "ai_agent",
            $or: [
              { "data.message": { $regex: /^ho000001:/ } },
              { "data.message": { $regex: /^un000001:/ } }
            ]
          }
        }
      ],
      as: "ai_messages"
    }
  },
  { $unwind: "$ai_messages" },
  {
    $addFields: {
      code_type: {
        $cond: {
          if: { $regexMatch: { input: "$ai_messages.data.message", regex: /^ho000001:/ } },
          then: "转人工(ho000001)",
          else: "转兜底(un000001)"
        }
      }
    }
  },
  {
    $facet: {
      // 按 code_type 分组统计
      by_type: [
        {
          $group: {
            _id: "$code_type",
            count: { $sum: 1 },
            sessions: { $addToSet: "$id" }
          }
        }
      ],
      // 统计总数
      total: [
        {
          $group: {
            _id: null,
            total_count: { $sum: 1 },
            total_sessions: { $addToSet: "$id" }
          }
        }
      ]
    }
  },
  { $unwind: "$total" },
  { $unwind: "$by_type" },
  {
    $project: {
      _id: 0,
      code_type: "$by_type._id",
      message_count: "$by_type.count",
      unique_session_count: { $size: "$by_type.sessions" },
      total_message_count: "$total.total_count",
      total_session_count: { $size: "$total.total_sessions" },
      message_ratio: {
        $concat: [
          { $toString: { $round: [{ $multiply: [{ $divide: ["$by_type.count", "$total.total_count"] }, 100] }, 2] } },
          "%"
        ]
      },
      session_ratio: {
        $concat: [
          { $toString: { $round: [{ $multiply: [{ $divide: [{ $size: "$by_type.sessions" }, { $size: "$total.total_sessions" }] }, 100] }, 2] } },
          "%"
        ]
      }
    }
  },
  { $sort: { code_type: 1 } }
]);
