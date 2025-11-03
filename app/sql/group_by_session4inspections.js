db.inspections.aggregate([
  {
    $group: {
      _id: "$session_id",
      count: { $sum: 1 },
      // 如果需要统计其他字段，可以添加：
      // firstInspection: { $first: "$$ROOT" },
      // lastInspection: { $last: "$$ROOT" },
      // inspectionIds: { $push: "$_id" }
    }
  },
  {
    $sort: { count: -1 }
  },
  {
    $project: {
      sessionId: "$_id",
      inspectionCount: "$count",
      _id: 0
    }
  }
])


// query by session_id 
db.sessions.find({
  // like query end with 3648
  _id: { $regex: "3648$" }
}).pretty()