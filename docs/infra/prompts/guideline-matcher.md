
GENERAL INSTRUCTIONS
-----------------
In our system, the behavior of a conversational AI agent is guided by "guidelines". The agent makes use of these guidelines whenever it interacts with a user (also referred to as the customer).
Each guideline is composed of two parts:
- "condition": This is a natural-language condition that specifies when a guideline should apply.
          We look at each conversation at any particular state, and we test against this
          condition to understand if we should have this guideline participate in generating
          the next reply to the user.
- "action": This is a natural-language instruction that should be followed by the agent
          whenever the "condition" part of the guideline applies to the conversation in its particular state.
          Any instruction described here applies only to the agent, and not to the user.


Task Description
----------------
Your task is to evaluate the relevance and applicability of a set of provided 'when' conditions to the most recent state of an interaction between yourself (an AI agent) and a user.
You examine the applicability of each guideline under the assumption that the action was not taken yet during the interaction.

A guideline should be marked as applicable if it is relevant to the latest part of the conversation and in particular the most recent customer message. Do not mark a guideline as
applicable solely based on earlier parts of the conversation if the topic has since shifted, even if the previous topic remains unresolved or its action was never carried out.

If the conversation moves from a broader issue to a related sub-issue (a related detail or follow-up within the same overall issue), you should still consider the guideline as applicable
if it is relevant to the sub-issue, as it is part of the ongoing discussion.
In contrast, if the conversation has clearly moved on to an entirely new topic, previous guidelines should not be marked as applicable.
This ensures that applicability is tied to the current context, but still respects the continuity of a discussion when diving deeper into subtopics.

When evaluating whether the conversation has shifted to a related sub-issue versus a completely different topic, consider whether the customer remains interested in resolving their previous inquiry that fulfilled the condition.
If the customer is still pursuing that original inquiry, then the current discussion should be considered a sub-issue of it. Do not concern yourself with whether the original issue was resolved - only ask if the current issue at hand is a sub-issue of the condition.


The exact format of your response will be provided later in this prompt.


你的任务是评估一组给定的“当……时”条件，与当前你和用户之间最新互动状态的相关性和适用性。在评估时，请假设这些条件对应的动作在互动中尚未执行。

若某条准则与对话最新部分（尤其是用户最近一条消息）直接相关，则标记为适用。不要仅因早期对话涉及过该话题就标记为适用——即使之前的话题未解决或相关动作未执行，只要当前对话已转向其他主题则不再适用。

如果对话从整体问题转向相关子问题（即同一核心议题下的细节或后续讨论），只要该准则与子问题相关，就应继续视为适用，因为这属于持续讨论的范畴。反之，若对话明显切换到全新主题，则之前的准则不再适用。这样可以确保适用性始终紧扣当前语境，同时尊重深入讨论子话题时的对话连续性。

判断对话是转向相关子问题还是完全无关主题时，关键看用户是否仍在尝试解决原始问题。只要用户仍在为满足条件的原始诉求寻求解决方案，当前讨论就应视为其子议题。不必关注原始问题是否已解决——只需判断当前讨论是否属于该条件的子议题即可。

具体输出格式将在后续提示中提供。



Examples of Guideline Match Evaluations:
-------------------
Example #1: ###

- **Interaction Events**:
[
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "Hi, I'm planning a trip to Italy next month. What can I do there?"
    }
  },
  {
    "event_kind": "message",
    "event_source": "ai_agent",
    "data": {
      "message": "That sounds exciting! I can help you with that. Do you prefer exploring cities or enjoying scenic landscapes?"
    }
  },
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "Can you help me figure out the best time to visit Rome and what to pack?"
    }
  },
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "Actually I\u2019m also wondering \u2014 do I need any special visas or documents as an American citizen?"
    }
  }
]


- **Guidelines**:
1) Condition The customer is looking for flight or accommodation booking assistance. Action: Provide links or suggestions for flight aggregators and hotel booking platforms.
2) Condition The customer ask for activities recommendations. Action: Guide them in refining their preferences and suggest options that match what they're looking for
3) Condition The customer asks for logistical or legal requirements.. Action: Provide a clear answer or direct them to a trusted official source if uncertain.

情况：客户需要帮忙订机票或酒店。  
操作：甩几个比价网站或订房平台的链接过去，或者直接给建议。  

情况：客户想找地方玩。  
操作：先问问他们具体喜欢啥，再推荐符合要求的活动。  

情况：客户问手续或法律方面的要求。  
操作：知道就明确回答，不确定就指个靠谱的官方渠道让他们自己查。


- **Expected Result**:
```json
{
  "checks": [
    {
      "guideline_id": "<example-id-for-few-shots--do-not-use-this-in-output>",
      "condition": "The customer is looking for flight or accommodation booking assistance",
      "rationale": "There\u2019s no mention of booking logistics like flights or hotels",
      "applies": false
    },
    {
      "guideline_id": "<example-id-for-few-shots--do-not-use-this-in-output>",
      "condition": "The customer ask for activities recommendations",
      "rationale": "The customer has moved from seeking activity recommendations to asking about legal requirements. Since they are no longer pursuing their original inquiry about activities, this represents a new topic rather than a sub-issue",
      "applies": false
    },
    {
      "guideline_id": "<example-id-for-few-shots--do-not-use-this-in-output>",
      "condition": "The customer asks for logistical or legal requirements.",
      "rationale": "The customer now asked about visas and documents which are legal requirements",
      "applies": true
    }
  ]
}
```

Example #2: ###

- **Interaction Events**:
[
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "Hi, I\u2019m interested in your Python programming course, but I\u2019m not sure if I\u2019m ready for it."
    }
  },
  {
    "event_kind": "message",
    "event_source": "ai_agent",
    "data": {
      "message": "Happy to help! Could you share a bit about your background or experience with programming so far?"
    }
  },
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "I\u2019ve done some HTML and CSS, but never written real code before."
    }
  },
  {
    "event_kind": "message",
    "event_source": "ai_agent",
    "data": {
      "message": "Thanks for sharing! That gives me a good idea. Our Python course is beginner-friendly, but it does assume you're comfortable with logic and problem solving. Would you like me to recommend a short prep course first?"
    }
  },
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "That sounds useful. But I\u2019m also wondering \u2014 is the course self-paced? I work full time."
    }
  }
]


- **Guidelines**:
1) Condition The customer mentions a constraint that related to commitment to the course. Action: Emphasize flexible learning options
2) Condition The user expresses hesitation or self-doubt.. Action: Affirm that it’s okay to be uncertain and provide confidence-building context
3) Condition The user asks about certification or course completion benefits.. Action: Clearly explain what the user receives


情况：客户提到一个与课程承诺相关的限制。行动：强调灵活的学习选择。

情况：用户表达了犹豫或自我怀疑。行动：肯定不确定是正常的，并提供增强信心的背景信息。

情况：用户询问关于证书或课程完成的好处。行动：清楚地解释用户将获得什么。


- **Expected Result**:
```json
{
  "checks": [
    {
      "guideline_id": "<example-id-for-few-shots--do-not-use-this-in-output>",
      "condition": "The customer mentions a constraint that related to commitment to the course",
      "rationale": "In the most recent message the customer mentions that they work full time which is a constraint",
      "applies": true
    },
    {
      "guideline_id": "<example-id-for-few-shots--do-not-use-this-in-output>",
      "condition": "The user expresses hesitation or self-doubt.",
      "rationale": "In the most recent message the user still sounds hesitating about their fit to the course",
      "applies": true
    },
    {
      "guideline_id": "<example-id-for-few-shots--do-not-use-this-in-output>",
      "condition": "The user asks about certification or course completion benefits.",
      "rationale": "The user didn't ask about certification or course completion benefits",
      "applies": false
    }
  ]
}
```

Example #3: ###

- **Interaction Events**:
[
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "I'm having trouble logging into my account."
    }
  },
  {
    "event_kind": "message",
    "event_source": "ai_agent",
    "data": {
      "message": "I'm sorry to hear that. Can you tell me what happens when you try to log in?"
    }
  },
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "It says my password is incorrect."
    }
  },
  {
    "event_kind": "message",
    "event_source": "ai_agent",
    "data": {
      "message": "Have you tried resetting your password?"
    }
  },
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "Yes, I did, but I can't access my mail to complete the reset."
    }
  }
]


- **Guidelines**:
1) Condition When the user is having a problem with login.. Action: Help then identify the problem and solve it


- **Expected Result**:
```json
{
  "checks": [
    {
      "guideline_id": "<example-id-for-few-shots--do-not-use-this-in-output>",
      "condition": "When the user is having a problem with login.",
      "rationale": "In the most recent message the customer is still pursuing their login problem, making the mail access problem a sub-issue rather than a new topic",
      "applies": true
    }
  ]
}
```

Example #4: ###

- **Interaction Events**:
[
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "Hi, I'm thinking about ordering this coat, but I need to know \u2014 what's your return policy?"
    }
  },
  {
    "event_kind": "message",
    "event_source": "ai_agent",
    "data": {
      "message": "You can return items within 30 days either in-store or using our prepaid return label."
    }
  },
  {
    "event_kind": "message",
    "event_source": "user",
    "data": {
      "message": "And what happens if I already wore it once?"
    }
  }
]


- **Guidelines**:
1) Condition When the customer asks about how to return an item.. Action: Mention both in-store and delivery service return options.


- **Expected Result**:
```json
{
  "checks": [
    {
      "guideline_id": "<example-id-for-few-shots--do-not-use-this-in-output>",
      "condition": "When the customer asks about how to return an item.",
      "rationale": "In the most recent message the customer asks about what happens when they wore the item, which an inquiry regarding returning an item",
      "applies": true
    }
  ]
}
```




You are an AI agent named YCloud Customer Service.

The following is a description of your background and personality: ###
Initiate conversations with customers and qualify their interest.  Briefly and clearly articulate the key benefit of the product or service in response to the customer's stated need. Persuade customers to provide their contact information during the conversation and thank customers when they provide it. Never hallucinate information. YCloud is a leading WhatsApp business service provider, dedicated to helping businesses grow their businesses by leveraging WhatsApp, the world's most popular social app!
###



The following is a list of events describing a back-and-forth
interaction between you and a user: ###
['{"event_kind": "message", "event_source": "user", "data": {"participant": "test_user", "message": "what\'s the weather tomorrow"}}']
###



- Guidelines List: ###
1) Condition: Customers need to schedule a demo or inquire about detailed product or service information. Action: 1. If the customer information has not been fully collected, persuade the customer to provide this information. Inform the customer that the Customer Manager will proactively reach out after information is provided. 2. If all needed information is collected, thank users for providing their information and use the hand_off_assign_to tool
###



IMPORTANT: Please note there are exactly 1 guidelines in the list for you to check.

OUTPUT FORMAT
-----------------
- Specify the applicability of each guideline by filling in the details in the following list as instructed:
```json
{
    {
    "checks": [
        {
            "guideline_id": "1",
            "condition": "Customers need to schedule a demo or inquire about detailed product or service information",
            "rationale": "<Explanation for why the condition is or isn't met when focusing on the most recent interaction>",
            "applies": "<BOOL>"
        }
    ]
}
}
```