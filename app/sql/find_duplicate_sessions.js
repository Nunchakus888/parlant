// 查找 sessions 集合中所有重复的 id
// 使用方法：mongo omni_agent_sessions find_duplicate_sessions.js

// 方法1: 查找所有重复的 id 及其出现次数
db.sessions.aggregate([
  {
    $group: {
      _id: "$id",
      count: { $sum: 1 },
      documents: { $push: "$$ROOT" }
    }
  },
  {
    $match: {
      count: { $gt: 1 }  // 只返回出现次数大于1的
    }
  },
  {
    $sort: { count: -1 }
  },
  {
    $project: {
      id: "$_id",
      count: 1,
      documents: 1,
      _id: 0
    }
  }
])

print("\n==================== 分隔线 ====================\n");

// 方法2: 只返回重复的 id 和数量（不包含完整文档，更快）
db.sessions.aggregate([
  {
    $group: {
      _id: "$id",
      count: { $sum: 1 },
      mongoIds: { $push: "$_id" }  // MongoDB 的 _id
    }
  },
  {
    $match: {
      count: { $gt: 1 }
    }
  },
  {
    $sort: { count: -1 }
  },
  {
    $project: {
      id: "$_id",
      duplicateCount: "$count",
      mongoIds: 1,
      _id: 0
    }
  }
])

print("\n==================== 分隔线 ====================\n");

// 方法3: 查看特定重复的 id（例如错误中的 "61065615704995840"）
db.sessions.find({
  id: "61065615704995840"
}).pretty()

// 删除指令: 删除空的重复记录（保留有 agent_states 的记录）
// db.sessions.deleteOne({_id: ObjectId("690a2f2c4ea5d6e231cdbc93")})  // 删除第二条（空记录）

// 或删除所有该 id 的记录
// db.sessions.deleteMany({id: "61065615704995840"})

print("\n==================== 分隔线 ====================\n");

// 方法4: 统计总体情况
var stats = {
  totalDocuments: db.sessions.count(),
  uniqueIds: db.sessions.distinct("id").length
};
stats.duplicates = stats.totalDocuments - stats.uniqueIds;
printjson(stats);

