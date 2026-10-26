"""Interactive official-endpoint setup; key input is hidden and saved with Windows DPAPI."""
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


if __name__ == '__main__':
    from campus.providers import test_provider, generate_answer
    from campus.credentials import save_key
    key = getpass.getpass('DeepSeek API key (hidden): ')
    config = {'base_url':'https://api.deepseek.com', 'model':'deepseek-flash', 'api_key':key}
    try:
        connection = test_provider(config)
        if not connection['selected_model_available']:
            raise ValueError('模型列表中没有 deepseek-flash，请核对官方模型。')
        answer, _ = generate_answer('请仅回答：连接成功。', [], config, messages=[
            {'role':'user', 'content':'请仅回答：连接成功。'}])
        save_key(ROOT / 'data', key, config['base_url'])
        path = ROOT / 'data/settings.json'
        settings = json.loads(path.read_text(encoding='utf-8'))
        settings.update(enabled=True, base_url=config['base_url'], model=config['model'], remember_key=True)
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        report = {'tested_at':datetime.now().astimezone().isoformat(), 'models':connection['models'],
                  'selected_model':config['model'], 'generation_probe':answer,
                  'key_storage':'Windows user-bound DPAPI', 'success':True}
        (ROOT/'data/model-connection-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    except ValueError as exc:
        print('Connection failed:', str(exc))
        raise SystemExit(1) from None
