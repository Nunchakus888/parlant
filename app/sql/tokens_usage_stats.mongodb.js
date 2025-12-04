// =====================================================
// MongoDB 查询脚本：按会话统计模型 Tokens 消耗和 Cached Tokens
// =====================================================

// -----------------------------------------------------
// 1. 按 session_id 统计总体 tokens 消耗
// -----------------------------------------------------
db.inspections.aggregate([
  {
    $group: {
      _id: "$session_id",
      inspection_count: { $sum: 1 },
      // 总计 tokens
      total_input_tokens: { $sum: "$usage_info.input_tokens" },
      total_output_tokens: { $sum: "$usage_info.output_tokens" },
      total_tokens: { $sum: "$usage_info.total_tokens" },
      // cached_tokens 统计
      total_cached_tokens: { $sum: "$usage_info.prompt_tokens_details.cached_tokens" },
      // reasoning_tokens 统计
      total_reasoning_tokens: { $sum: "$usage_info.completion_tokens_details.reasoning_tokens" },
      // 时间范围
      first_creation: { $min: "$creation_utc" },
      last_creation: { $max: "$creation_utc" }
    }
  },
  {
    $sort: { total_tokens: -1 }
  },
  {
    $project: {
      session_id: "$_id",
      inspection_count: 1,
      tokens: {
        input: "$total_input_tokens",
        output: "$total_output_tokens",
        total: "$total_tokens",
        cached: "$total_cached_tokens",
        reasoning: "$total_reasoning_tokens"
      },
      time_range: {
        first: "$first_creation",
        last: "$last_creation"
      },
      _id: 0
    }
  }
])

// -----------------------------------------------------
// 2. 查询特定 session_id 的详细 tokens 消耗
// -----------------------------------------------------
db.inspections.aggregate([
  {
    $match: {
      session_id: "Fa7nCvbVKfBPqrrE9iPuJ"  // 替换为实际的 session_id
    }
  },
  {
    $project: {
      session_id: 1,
      correlation_id: 1,
      creation_utc: 1,
      // 总体 usage
      "usage_info.input_tokens": 1,
      "usage_info.output_tokens": 1,
      "usage_info.total_tokens": 1,
      "usage_info.prompt_tokens_details.cached_tokens": 1,
      "usage_info.completion_tokens_details.reasoning_tokens": 1,
      // message_generations 中的 usage
      message_generations_count: { $size: { $ifNull: ["$message_generations", []] } },
      preparation_iterations_count: { $size: { $ifNull: ["$preparation_iterations", []] } }
    }
  },
  {
    $sort: { creation_utc: 1 }
  }
])

// -----------------------------------------------------
// 3. 按 correlation_id 前缀（去除 ::process 后缀）分组统计
// -----------------------------------------------------
db.inspections.aggregate([
  {
    $addFields: {
      // 提取 correlation_id 的主 ID 部分
      main_correlation_id: {
        $arrayElemAt: [{ $split: ["$correlation_id", "::"] }, 0]
      }
    }
  },
  {
    $group: {
      _id: {
        session_id: "$session_id",
        main_correlation_id: "$main_correlation_id"
      },
      process_count: { $sum: 1 },
      total_input_tokens: { $sum: "$usage_info.input_tokens" },
      total_output_tokens: { $sum: "$usage_info.output_tokens" },
      total_tokens: { $sum: "$usage_info.total_tokens" },
      total_cached_tokens: { $sum: "$usage_info.prompt_tokens_details.cached_tokens" }
    }
  },
  {
    $sort: { total_tokens: -1 }
  },
  {
    $project: {
      session_id: "$_id.session_id",
      correlation_id: "$_id.main_correlation_id",
      process_count: 1,
      tokens: {
        input: "$total_input_tokens",
        output: "$total_output_tokens",
        total: "$total_tokens",
        cached: "$total_cached_tokens"
      },
      _id: 0
    }
  }
])

// -----------------------------------------------------
// 4. 统计 cached_tokens 命中率（按会话）
// -----------------------------------------------------
db.inspections.aggregate([
  {
    $group: {
      _id: "$session_id",
      total_input_tokens: { $sum: "$usage_info.input_tokens" },
      total_cached_tokens: { $sum: "$usage_info.prompt_tokens_details.cached_tokens" }
    }
  },
  {
    $addFields: {
      cache_hit_rate: {
        $cond: {
          if: { $gt: ["$total_input_tokens", 0] },
          then: {
            $multiply: [
              { $divide: ["$total_cached_tokens", "$total_input_tokens"] },
              100
            ]
          },
          else: 0
        }
      }
    }
  },
  {
    $sort: { cache_hit_rate: -1 }
  },
  {
    $project: {
      session_id: "$_id",
      total_input_tokens: 1,
      total_cached_tokens: 1,
      cache_hit_rate: { $round: ["$cache_hit_rate", 2] },
      _id: 0
    }
  }
])

// -----------------------------------------------------
// 5. 全局 tokens 消耗统计汇总
// -----------------------------------------------------
db.inspections.aggregate([
  {
    $group: {
      _id: null,
      total_inspections: { $sum: 1 },
      unique_sessions: { $addToSet: "$session_id" },
      // 总计 tokens
      total_input_tokens: { $sum: "$usage_info.input_tokens" },
      total_output_tokens: { $sum: "$usage_info.output_tokens" },
      total_tokens: { $sum: "$usage_info.total_tokens" },
      // cached_tokens 统计
      total_cached_tokens: { $sum: "$usage_info.prompt_tokens_details.cached_tokens" },
      // reasoning_tokens 统计
      total_reasoning_tokens: { $sum: "$usage_info.completion_tokens_details.reasoning_tokens" }
    }
  },
  {
    $project: {
      _id: 0,
      total_inspections: 1,
      unique_session_count: { $size: "$unique_sessions" },
      tokens: {
        input: "$total_input_tokens",
        output: "$total_output_tokens",
        total: "$total_tokens",
        cached: "$total_cached_tokens",
        reasoning: "$total_reasoning_tokens"
      },
      cache_hit_rate: {
        $cond: {
          if: { $gt: ["$total_input_tokens", 0] },
          then: {
            $round: [
              {
                $multiply: [
                  { $divide: ["$total_cached_tokens", "$total_input_tokens"] },
                  100
                ]
              },
              2
            ]
          },
          else: 0
        }
      }
    }
  }
])

// -----------------------------------------------------
// 6. 按时间范围统计 tokens 消耗（按天）
// -----------------------------------------------------
db.inspections.aggregate([
  {
    $addFields: {
      creation_date: {
        $dateToString: {
          format: "%Y-%m-%d",
          date: { $toDate: "$creation_utc" }
        }
      }
    }
  },
  {
    $group: {
      _id: "$creation_date",
      inspection_count: { $sum: 1 },
      unique_sessions: { $addToSet: "$session_id" },
      total_input_tokens: { $sum: "$usage_info.input_tokens" },
      total_output_tokens: { $sum: "$usage_info.output_tokens" },
      total_tokens: { $sum: "$usage_info.total_tokens" },
      total_cached_tokens: { $sum: "$usage_info.prompt_tokens_details.cached_tokens" }
    }
  },
  {
    $sort: { _id: -1 }
  },
  {
    $project: {
      date: "$_id",
      inspection_count: 1,
      session_count: { $size: "$unique_sessions" },
      tokens: {
        input: "$total_input_tokens",
        output: "$total_output_tokens",
        total: "$total_tokens",
        cached: "$total_cached_tokens"
      },
      _id: 0
    }
  }
])

// -----------------------------------------------------
// 7. 详细展开 message_generations 和 preparation_iterations 的 tokens
// -----------------------------------------------------
db.inspections.aggregate([
  {
    $match: {
      session_id: "Fa7nCvbVKfBPqrrE9iPuJ"  // 替换为实际的 session_id
    }
  },
  {
    $project: {
      session_id: 1,
      correlation_id: 1,
      // 展开 message_generations 中的 usage
      message_gen_usages: {
        $reduce: {
          input: "$message_generations",
          initialValue: [],
          in: {
            $concatArrays: [
              "$$value",
              {
                $map: {
                  input: "$$this.generations",
                  as: "gen",
                  in: {
                    schema_name: "$$gen.schema_name",
                    model: "$$gen.model",
                    input_tokens: "$$gen.usage.input_tokens",
                    output_tokens: "$$gen.usage.output_tokens",
                    cached_tokens: "$$gen.usage.prompt_tokens_details.cached_tokens"
                  }
                }
              }
            ]
          }
        }
      },
      // 展开 preparation_iterations 中的 guideline_match usage
      guideline_match_usages: {
        $map: {
          input: "$preparation_iterations",
          as: "iter",
          in: {
            $map: {
              input: "$$iter.generations.guideline_match.batches",
              as: "batch",
              in: {
                schema_name: "$$batch.schema_name",
                model: "$$batch.model",
                input_tokens: "$$batch.usage.input_tokens",
                output_tokens: "$$batch.usage.output_tokens",
                cached_tokens: "$$batch.usage.prompt_tokens_details.cached_tokens"
              }
            }
          }
        }
      },
      // usage_info 汇总
      usage_info: 1
    }
  }
])

