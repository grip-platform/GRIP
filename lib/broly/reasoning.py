"""
Broly Reasoning Router - 7 framework implementations as prompt generators.

Concept: a collaborator (compressed information language specification)
Implementation: GRIP framework

Each reasoning framework generates structured prompts that guide agent
behavior. At higher power levels, some frameworks map to team patterns:
    - ToT -> council pattern (cluster+)
    - GoT -> graph traversal via teammates (cluster+)
    - BoT -> swarm with scoring (cluster+)

Frameworks:
    1. CoT  (Chain of Thought)  - Sequential step-by-step reasoning
    2. ToT  (Tree of Thought)   - Parallel branch exploration
    3. GoT  (Graph of Thought)  - Non-linear concept traversal
    4. BoT  (Bag of Thought)    - Independent parallel analysis with merge
    5. SelfAsk                  - Recursive question decomposition
    6. ReAct                    - Reason + Act + Observe loop
    7. Reflexion                - Post-execution reflection and learning
"""

import re
from typing import Any, Dict, List, Optional

from .power_levels import PowerLevel, can_use_reasoning


class ReasoningAccessDenied(PermissionError):
    """Raised when a reasoning framework is not available at current power level."""
    pass


class ReasoningRouter:
    """
    Routes tasks to appropriate reasoning frameworks based on
    task characteristics and power level.
    """

    # Keywords that suggest each framework
    FRAMEWORK_SIGNALS = {
        "tot": [
            "compare", "options", "alternatives", "trade-off", "pros and cons",
            "evaluate", "choose between", "which approach", "best way",
        ],
        "got": [
            "related to", "connected", "depends on", "relationship",
            "how does X affect Y", "impact", "chain", "cascade",
        ],
        "bot": [
            "brainstorm", "many ideas", "creative", "explore all",
            "comprehensive", "exhaustive", "all angles",
        ],
        "selfask": [
            "break down", "decompose", "what do I need to know",
            "prerequisite", "step by step", "first need to",
        ],
        "react": [
            "investigate", "debug", "find out", "check", "verify",
            "look at", "examine", "test hypothesis",
        ],
        "reflexion": [
            "what went wrong", "improve", "lesson learned",
            "next time", "retrospective", "what worked",
        ],
    }

    def __init__(self, power_level: PowerLevel = PowerLevel.THREAD):
        self._level = power_level

    def select_framework(self, task_description: str) -> str:
        """
        Auto-select the best reasoning framework for a task.

        Args:
            task_description: Natural language task description

        Returns:
            Framework name (e.g., "cot", "tot", "got")
        """
        task_lower = task_description.lower()
        scores: Dict[str, int] = {}

        for framework, signals in self.FRAMEWORK_SIGNALS.items():
            if not can_use_reasoning(self._level, framework):
                continue
            score = sum(1 for signal in signals if signal in task_lower)
            if score > 0:
                scores[framework] = score

        if scores:
            return max(scores, key=scores.get)

        # Default to CoT (always available)
        return "cot"

    def generate_prompt(
        self,
        framework: str,
        task: str,
        context: Optional[str] = None,
        memory_context: Optional[str] = None,
    ) -> str:
        """
        Generate a structured prompt for the specified framework.

        Args:
            framework: Framework name
            task: Task description
            context: Optional additional context
            memory_context: Optional memory retrieval results

        Returns:
            Structured prompt string
        """
        if not can_use_reasoning(self._level, framework):
            raise ReasoningAccessDenied(
                f"Framework '{framework}' requires higher power level. "
                f"Current: {self._level.value}"
            )

        generators = {
            "cot": self._prompt_cot,
            "tot": self._prompt_tot,
            "got": self._prompt_got,
            "bot": self._prompt_bot,
            "selfask": self._prompt_selfask,
            "react": self._prompt_react,
            "reflexion": self._prompt_reflexion,
        }

        generator = generators.get(framework, self._prompt_cot)
        return generator(task, context, memory_context)

    # =========================================================================
    # FRAMEWORK PROMPT GENERATORS
    # =========================================================================

    def _prompt_cot(self, task: str, context: Optional[str], memory: Optional[str]) -> str:
        """Chain of Thought - sequential step-by-step reasoning."""
        parts = ["## Chain of Thought Analysis", ""]
        if context:
            parts.append(f"**Context:** {context}")
            parts.append("")
        if memory:
            parts.append(f"**Relevant Memory:** {memory}")
            parts.append("")
        parts.extend([
            f"**Task:** {task}",
            "",
            "Think through this step by step:",
            "1. Identify the core problem",
            "2. List known facts and constraints",
            "3. Reason through each step sequentially",
            "4. Arrive at a conclusion with justification",
            "",
            "Show your reasoning at each step.",
        ])
        return "\n".join(parts)

    def _prompt_tot(self, task: str, context: Optional[str], memory: Optional[str]) -> str:
        """Tree of Thought - parallel branch exploration with evaluation."""
        parts = ["## Tree of Thought Exploration", ""]
        if context:
            parts.append(f"**Context:** {context}")
            parts.append("")
        if memory:
            parts.append(f"**Relevant Memory:** {memory}")
            parts.append("")
        parts.extend([
            f"**Task:** {task}",
            "",
            "Explore multiple approaches in parallel:",
            "",
            "### Branch A: [First Approach]",
            "- Reasoning: ...",
            "- Pros: ...",
            "- Cons: ...",
            "- Confidence: X/10",
            "",
            "### Branch B: [Second Approach]",
            "- Reasoning: ...",
            "- Pros: ...",
            "- Cons: ...",
            "- Confidence: X/10",
            "",
            "### Branch C: [Third Approach]",
            "- Reasoning: ...",
            "- Pros: ...",
            "- Cons: ...",
            "- Confidence: X/10",
            "",
            "### Evaluation",
            "Compare branches and select the best path. Justify your selection.",
        ])
        return "\n".join(parts)

    def _prompt_got(self, task: str, context: Optional[str], memory: Optional[str]) -> str:
        """Graph of Thought - non-linear concept traversal."""
        parts = ["## Graph of Thought Traversal", ""]
        if context:
            parts.append(f"**Context:** {context}")
            parts.append("")
        if memory:
            parts.append(f"**Relevant Memory:** {memory}")
            parts.append("")
        parts.extend([
            f"**Task:** {task}",
            "",
            "Map the conceptual relationships:",
            "",
            "### Concept Nodes",
            "Identify the key concepts involved.",
            "",
            "### Edges (Relationships)",
            "How do these concepts connect? What affects what?",
            "",
            "### Traversal Path",
            "Navigate the concept graph to find insights:",
            "- Start node: ...",
            "- Follow edge: ... (because ...)",
            "- Reach node: ... (insight: ...)",
            "- Follow edge: ... (because ...)",
            "",
            "### Synthesis",
            "What does the graph traversal reveal about the task?",
        ])
        return "\n".join(parts)

    def _prompt_bot(self, task: str, context: Optional[str], memory: Optional[str]) -> str:
        """Bag of Thought - independent parallel analysis with scoring."""
        parts = ["## Bag of Thought Analysis", ""]
        if context:
            parts.append(f"**Context:** {context}")
            parts.append("")
        if memory:
            parts.append(f"**Relevant Memory:** {memory}")
            parts.append("")
        parts.extend([
            f"**Task:** {task}",
            "",
            "Generate independent analyses from different angles:",
            "",
            "### Perspective 1: [Technical Feasibility]",
            "Analysis: ...",
            "Score: X/10",
            "",
            "### Perspective 2: [User Impact]",
            "Analysis: ...",
            "Score: X/10",
            "",
            "### Perspective 3: [Maintenance Cost]",
            "Analysis: ...",
            "Score: X/10",
            "",
            "### Perspective 4: [Risk Assessment]",
            "Analysis: ...",
            "Score: X/10",
            "",
            "### Weighted Merge",
            "Combine perspectives into a unified recommendation.",
            "Final score: (weighted average)",
        ])
        return "\n".join(parts)

    def _prompt_selfask(self, task: str, context: Optional[str], memory: Optional[str]) -> str:
        """SelfAsk - recursive question decomposition."""
        parts = ["## SelfAsk Decomposition", ""]
        if context:
            parts.append(f"**Context:** {context}")
            parts.append("")
        if memory:
            parts.append(f"**Relevant Memory:** {memory}")
            parts.append("")
        parts.extend([
            f"**Task:** {task}",
            "",
            "Decompose into sub-questions:",
            "",
            "**Q1:** What is the core question to answer?",
            "**A1:** ...",
            "",
            "**Follow-up Q1a:** [Sub-question from A1]",
            "**A1a:** ...",
            "",
            "**Follow-up Q1b:** [Another sub-question]",
            "**A1b:** ...",
            "",
            "**Q2:** What prerequisite knowledge is needed?",
            "**A2:** ...",
            "",
            "Continue decomposing until all sub-questions are answered,",
            "then synthesize into a final answer.",
            "",
            "**Final Answer:** ...",
        ])
        return "\n".join(parts)

    def _prompt_react(self, task: str, context: Optional[str], memory: Optional[str]) -> str:
        """ReAct - Reason + Act + Observe loop."""
        parts = ["## ReAct Loop", ""]
        if context:
            parts.append(f"**Context:** {context}")
            parts.append("")
        if memory:
            parts.append(f"**Relevant Memory:** {memory}")
            parts.append("")
        parts.extend([
            f"**Task:** {task}",
            "",
            "Execute a Reason-Act-Observe loop:",
            "",
            "### Iteration 1",
            "**Thought:** What do I need to find out first?",
            "**Action:** [tool call or investigation step]",
            "**Observation:** [result of action]",
            "",
            "### Iteration 2",
            "**Thought:** Based on observation, what next?",
            "**Action:** [next step]",
            "**Observation:** [result]",
            "",
            "Continue until the task is resolved.",
            "",
            "### Conclusion",
            "**Final Thought:** Summarize findings and solution.",
        ])
        return "\n".join(parts)

    def _prompt_reflexion(self, task: str, context: Optional[str], memory: Optional[str]) -> str:
        """Reflexion - post-execution reflection and learning extraction."""
        parts = ["## Reflexion Analysis", ""]
        if context:
            parts.append(f"**Context:** {context}")
            parts.append("")
        if memory:
            parts.append(f"**Relevant Memory:** {memory}")
            parts.append("")
        parts.extend([
            f"**Task:** {task}",
            "",
            "Reflect on the execution:",
            "",
            "### What Happened",
            "Describe the actions taken and their outcomes.",
            "",
            "### What Worked",
            "Identify successful strategies and decisions.",
            "",
            "### What Failed",
            "Identify failures, errors, or suboptimal choices.",
            "",
            "### Root Cause Analysis",
            "Why did failures occur? What assumptions were wrong?",
            "",
            "### Lessons Learned",
            "Extract generalizable principles for future sessions.",
            "",
            "### Memory Update",
            "What should be stored in long-term memory?",
        ])
        return "\n".join(parts)
