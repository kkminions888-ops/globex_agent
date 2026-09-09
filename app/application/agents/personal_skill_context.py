"""个人 Skill 正文只在使用轮保留；清理历史工具输出，不破坏调用配对。"""
import json


def clear_personal_skill_outputs(messages):
    for message in messages:
        for block in message.content:
            if getattr(block,"type",None) != "tool_result" or block.name != "load_agent_skill_tool":
                continue
            output=block.output
            text=output if isinstance(output,str) else "".join(getattr(part,"text","") for part in output)
            try:
                doc=json.loads(text)
            except (ValueError,TypeError):
                continue
            if isinstance(doc,dict) and doc.get("source")=="buyer":
                block.output="历史个人 Skill 正文已清理。需要使用时按本轮 personal_skill_catalog 的版本重新读取。"
