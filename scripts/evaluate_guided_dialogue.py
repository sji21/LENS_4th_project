"""Real local planner regression with a fixed legal backend; not a legal-quality evaluation."""
import os, json, time, argparse
from pathlib import Path
from unittest.mock import patch, Mock
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.test.utils import override_settings
from chat import services, dialogue_planner
from src.generation.models import Answer
from src.evaluation.chatting_run import fingerprints
from src.evaluation.chatting_provenance import capture_ollama_identity
from src.generation.llm import LLM_MODEL
from copy import deepcopy
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
out = parser.parse_args().output
assert not out.exists()
report = {'mode': 'real_planner_fixed_legal_backend', 'files': fingerprints(), 'model': capture_ollama_identity(LLM_MODEL), 'rows': []}
assert report['model']['available']
from src.evaluation.chatting_plan_run import ObservedModel
create = dialogue_planner.create_planner_model
models = []

def observed(ids):
    m = ObservedModel(create(ids))
    models.append(m)
    return m
query = []

def answer(q, *args, **kwargs):
    query.append(q)
    return Answer(question=q, status='answered', text='검증 경계를 대체한 평가용 안내')
loader = Mock()
loader.result.return_value = None
cases = [('consultation', ['전세 계약이 끝나가는데 보증금을 아직 돌려받지 못했어', '다음 달 말이고, 지난주에 문자로 갱신하지 않겠다고 알렸어요.', '지금 상황을 짧게 정리해 주세요.']), ('general', ['대항력이 무슨 뜻인지 설명해 주세요.'])]
with patch.object(dialogue_planner, 'create_planner_model', side_effect=observed), override_settings(CHAT_CONVERSATION_ENABLED=True), patch.object(services, 'retrieval_loader', return_value=loader), patch.object(services.graph, 'answer_question', side_effect=answer):
    for (name, turns) in cases:
        state = services.initial_state()
        for user in turns:
            t = time.perf_counter()
            query.clear()
            models.clear()
            message = services.respond(state, user)
            row = {'case': name, 'input': user, 'raw_proposal': models[-1].proposal if models else None, 'message': message, 'dialogue': deepcopy(state['dialogue']), 'query': query[:], 'seconds': round(time.perf_counter() - t, 3)}
            report['rows'].append(row)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(name, message['action'], message.get('followup_question'), flush=True)
report['source_unchanged'] = report['files'] == fingerprints()
(first, second, third, general) = report['rows']
report['checks'] = {'initial_guidance_with_question': first['message']['action'] == 'rag' and first['dialogue']['pending'] is not None, 'not_ended': first['dialogue']['facts'].get('contract_ended', {}).get('value') == '아니요', 'date_bound': second['dialogue']['facts'].get('end_date', {}).get('value') == '다음 달 말', 'no_reask_supplied_fields': not second['dialogue']['pending'] or second['dialogue']['pending']['field'] not in {'end_date', 'landlord_notified', 'notice_date'}, 'summary_no_interview': third['message']['action'] == 'rag' and third['message']['intent'] == 'explain' and (not third['dialogue']['pending']), 'general_no_interview': not general['message'].get('followup_question')}
out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(report['checks'], flush=True)
assert report['source_unchanged'] and all(report['checks'].values())
