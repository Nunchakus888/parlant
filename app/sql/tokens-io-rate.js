// =====================================================
// MongoDB 查询脚本：Tokens 输入/输出比例及缓存详情统计
// =====================================================

// -----------------------------------------------------
// 全局统计：输入/输出 tokens 详细比例 + 缓存命中率（最近一个月）
// -----------------------------------------------------


db.inspections.aggregate([
  {
    $match: {
      creation_utc: {
        $gte: "2025-10-20T00:00:00.000Z",
        $lte: "2025-12-19T23:59:59.999Z"
      }
    }
  },
  {
    $group: {
      _id: null,
      total_inspections: { $sum: 1 },
      unique_sessions: { $addToSet: "$session_id" },
      // 时间范围
      first_creation: { $min: "$creation_utc" },
      last_creation: { $max: "$creation_utc" },
      // 输入 tokens 统计
      total_input_tokens: { $sum: "$usage_info.input_tokens" },
      input_cached_tokens: { $sum: "$usage_info.prompt_tokens_details.cached_tokens" },
      // 输出 tokens 统计
      total_output_tokens: { $sum: "$usage_info.output_tokens" },
      output_cached_tokens: { $sum: "$usage_info.completion_tokens_details.cached_tokens" },
      // 总计
      total_tokens: { $sum: "$usage_info.total_tokens" }
    }
  },
  {
    $addFields: {
      // 计算非缓存的 tokens
      input_uncached_tokens: { $subtract: ["$total_input_tokens", "$input_cached_tokens"] },
      output_uncached_tokens: { $subtract: ["$total_output_tokens", "$output_cached_tokens"] },
      // 总缓存
      total_cached_tokens: { $add: ["$input_cached_tokens", "$output_cached_tokens"] },
      // session 数量
      session_count: { $size: "$unique_sessions" }
    }
  },
  {
    $project: {
      _id: 0,
      // 汇总信息
      total_inspections: "$total_inspections",
      unique_sessions: { $size: "$unique_sessions" },
      time_from: "$first_creation",
      time_to: "$last_creation",
      // 输入详情
      input_total: "$total_input_tokens",
      input_cached: "$input_cached_tokens",
      input_cache_hit_rate: {
        $cond: {
          if: { $gt: ["$total_input_tokens", 0] },
          then: { $round: [{ $multiply: [{ $divide: ["$input_cached_tokens", "$total_input_tokens"] }, 100] }, 2] },
          else: 0
        }
      },
      // 输出
      output_total: "$total_output_tokens",
      // 总计
      total_tokens: "$total_tokens",
      total_cached: "$total_cached_tokens",
      total_cache_hit_rate: {
        $cond: {
          if: { $gt: ["$total_tokens", 0] },
          then: { $round: [{ $multiply: [{ $divide: ["$total_cached_tokens", "$total_tokens"] }, 100] }, 2] },
          else: 0
        }
      },
      // 平均每个 session 的轮次（对话轮数）
      avg_rounds_per_session: {
        $cond: {
          if: { $gt: ["$session_count", 0] },
          then: { $round: [{ $divide: ["$total_inspections", "$session_count"] }, 2] },
          else: 0
        }
      },
      // 平均每个 session 的 tokens 消耗
      avg_tokens_per_session: {
        $cond: {
          if: { $gt: ["$session_count", 0] },
          then: { $round: [{ $divide: ["$total_tokens", "$session_count"] }, 0] },
          else: 0
        }
      },
      // 平均每次对话（每轮）的 tokens 消耗
      avg_tokens_per_round: {
        $cond: {
          if: { $gt: ["$total_inspections", 0] },
          then: { $round: [{ $divide: ["$total_tokens", "$total_inspections"] }, 0] },
          else: 0
        }
      },
      // 比例统计
      input_output_ratio: {
        $cond: {
          if: { $gt: ["$total_output_tokens", 0] },
          then: { $round: [{ $divide: ["$total_input_tokens", "$total_output_tokens"] }, 2] },
          else: "N/A"
        }
      },
      input_percentage: {
        $cond: {
          if: { $gt: ["$total_tokens", 0] },
          then: { $round: [{ $multiply: [{ $divide: ["$total_input_tokens", "$total_tokens"] }, 100] }, 2] },
          else: 0
        }
      },
      output_percentage: {
        $cond: {
          if: { $gt: ["$total_tokens", 0] },
          then: { $round: [{ $multiply: [{ $divide: ["$total_output_tokens", "$total_tokens"] }, 100] }, 2] },
          else: 0
        }
      }
    }
  }
])

/*
示例输出（平铺格式，方便表格阅读）:
{
  "total_inspections": 1234,
  "unique_sessions": 56,
  "time_from": "2024-11-19T00:00:00.000Z",
  "time_to": "2024-12-19T23:59:59.000Z",
  "input_total": 5000000,
  "input_cached": 2500000,
  "input_cache_hit_rate": 50,
  "output_total": 500000,
  "total_tokens": 5500000,
  "total_cached": 2500000,
  "total_cache_hit_rate": 45.45,
  "avg_rounds_per_session": 22.04,
  "avg_tokens_per_session": 98214,
  "avg_tokens_per_round": 4460,
  "input_output_ratio": 10,
  "input_percentage": 90.91,
  "output_percentage": 9.09
}

字段说明:
- input_total:             输入 tokens 总量
- input_cached:            输入缓存命中
- input_cache_hit_rate:    输入缓存命中率 (%)
- output_total:            输出 tokens 总量
- total_tokens:            总 tokens
- total_cached:            总缓存命中
- total_cache_hit_rate:    总缓存命中率 (%)
- avg_rounds_per_session:  平均每个 session 对话轮次
- avg_tokens_per_session:  平均每个 session 总消耗
- avg_tokens_per_round:    平均每轮对话消耗
- input_output_ratio:      输入/输出比
- input_percentage:        输入占总量 (%)
- output_percentage:       输出占总量 (%)
*/


// 注：OpenAI API 目前仅对输入(prompt)提供缓存，输出缓存通常为 0
