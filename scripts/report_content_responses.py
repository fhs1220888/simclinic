"""Post-study response and archived input observations; no new model/solver run."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simclinic.core.report import write_json
from simclinic.domains.openfoam.completion import canonical_dictionary
from simclinic.input_content import InputContentError, decode_foamfile_response


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    frozen = json.loads((batch / 'freeze.json').read_text())
    progress = json.loads((batch / 'progress.json').read_text())
    if progress['status'] != 'complete':
        raise ValueError('Wait for the frozen study to finish; do not tune from held observations')
    if digest(batch / 'freeze.json') != (batch / 'freeze.sha256').read_text().strip():
        raise ValueError('Freeze changed')
    if digest(ROOT / 'simclinic/input_content.py') != frozen['content_helper_sha256']:
        raise ValueError('Decoder differs from the predeclared candidate')
    rows, log_rows = [], []
    with tempfile.TemporaryDirectory(prefix='simclinic-content-observation-') as temporary:
        decoded_file = Path(temporary) / 'decoded'
        for entry in progress['runs']:
            out = Path(entry['directory'])
            record_path = out / 'record.json'
            if not record_path.is_file():
                rows.append(dict(run=entry['id'], arm=entry['arm'], missing_record=True))
                continue
            record = json.loads(record_path.read_text())
            attempts = record['attempts']
            first = out / attempts[0]['input_snapshot'] if attempts else None
            for request_path in sorted((out / 'model-calls').glob('*/request.json')):
                request = json.loads(request_path.read_text())
                system = request.get('system', '')
                name = re.findall(r'<file_name>([^<]+)</file_name>', system)
                folder = re.findall(r'<folder_name>([^<]+)</folder_name>', system)
                if len(name) != 1 or len(folder) != 1:
                    continue  # Initial single-file generation only; rewrites stay separate.
                relative = Path(folder[0]) / name[0]
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Unexpected file target: ' + str(relative))
                response_path = request_path.parent / 'response.json'
                response = json.loads(response_path.read_text()).get('text') if response_path.is_file() else None
                if not isinstance(response, str):
                    rows.append(dict(run=entry['id'], arm=entry['arm'], call=request_path.parent.name,
                                     target=str(relative), response_shape=None, response_unavailable=True))
                    continue
                stripped = response.lstrip()
                shape = ('fenced' if stripped.startswith('```') else
                         'json_like' if stripped.startswith(('{', '[', '"')) else 'raw')
                snapshot = first / relative if first else None
                reference = batch / 'tasks' / entry['task'] / 'acceptance-reference' / relative
                item = dict(run=entry['id'], arm=entry['arm'], call=request_path.parent.name,
                            target=str(relative), response_shape=shape,
                            response_path=str(response_path.relative_to(batch)),
                            response_sha256=digest(response_path),
                            response_text_sha256=hashlib.sha256(response.encode('utf-8')).hexdigest(),
                            first_snapshot_path=str(snapshot.relative_to(batch)) if snapshot else None,
                            first_snapshot_sha256=digest(snapshot) if snapshot and snapshot.is_file() else None,
                            raw_reply_equals_first_saved_input=(snapshot.read_text() == response)
                            if snapshot and snapshot.is_file() else None)
                try:
                    body = decode_foamfile_response(response, file_name=name[0], folder_name=folder[0])
                    decoded_file.write_text(body)
                    item.update(predeclared_decoder_accepts=True,
                                decoded_body_equals_first_saved_input=(snapshot.read_text() == body)
                                if snapshot and snapshot.is_file() else None,
                                decoded_tokens_match_task_reference=canonical_dictionary(decoded_file)
                                == canonical_dictionary(reference) if reference.is_file() else None)
                except InputContentError as exc:
                    item.update(predeclared_decoder_accepts=False, decoder_error=str(exc))
                rows.append(item)
            for log in sorted(out.rglob('*.log')):
                text = log.read_text(errors='replace')
                fatal = [i for i, line in enumerate(text.splitlines(), 1) if 'FOAM FATAL' in line]
                if fatal:
                    log_rows.append(dict(run=entry['id'], path=str(log.relative_to(batch)),
                                         sha256=digest(log), fatal_lines=fatal,
                                         end_marker_observed=bool(re.search(r'^End\s*$', text, re.M))))
            for log in sorted((out / 'attempts').glob('*/logs/log.*')):
                text = log.read_text(errors='replace')
                fatal = [i for i, line in enumerate(text.splitlines(), 1) if 'FOAM FATAL' in line]
                if fatal:
                    log_rows.append(dict(run=entry['id'], path=str(log.relative_to(batch)),
                                         sha256=digest(log), fatal_lines=fatal,
                                         end_marker_observed=bool(re.search(r'^End\s*$', text, re.M))))
    groups = {}
    for arm in ('baseline', 'candidate'):
        group = [r for r in rows if r['arm'] == arm]
        groups[arm] = dict(missing_records=sum(r.get('missing_record', False) for r in group),
                           unavailable_responses=sum(r.get('response_unavailable', False) for r in group),
                           initial_file_responses=sum('response_shape' in r for r in group),
                           shapes={shape: sum(r.get('response_shape') == shape for r in group)
                                   for shape in ('raw', 'json_like', 'fenced')})
    result = dict(offline_observation_only=True, new_model_or_simulation_run=False,
                  primary_analysis_unchanged=True, development_excluded=True,
                  decoder_sha256=frozen['content_helper_sha256'], groups=groups,
                  responses=rows, fatal_log_observations=log_rows,
                  limitation='Offline decoding of a baseline reply is a counterfactual interface check, '
                             'not a solver retest or another independent held observation.')
    write_json(batch / 'content-observations.json', result)
    lines = ['# 文件正文接口的离线证据补充', '',
             '本文件在冻结评估结束后生成，没有新增模型/求解调用，没有修改主要结果。开发检查不计入表格。', '',
             '| 组别 | 初次文件回复 | 原始正文 | JSON 外形 | 完整代码围栏 | 缺失运行记录 |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for arm, group in groups.items():
        shapes = group['shapes']
        lines.append(f"| {arm} | {group['initial_file_responses']} | {shapes['raw']} | {shapes['json_like']} | {shapes['fenced']} | {group['missing_records']} |")
    lines += ['', 'JSON 外形不自动表示有效封装；下面逐项使用冻结前的候选解码器检查身份与正文。', '',
              '| 运行 / 调用 | 文件 | 冻结解码器接受 | 原回复等于首次落盘 | 解包正文等于首次落盘 | 解包内容符合任务参考 |',
              '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        if row.get('response_shape') in ('json_like', 'fenced'):
            lines.append(f"| {row['run']} / {row['call']} | {row['target']} | {row.get('predeclared_decoder_accepts')} | {row.get('raw_reply_equals_first_saved_input')} | {row.get('decoded_body_equals_first_saved_input')} | {row.get('decoded_tokens_match_task_reference')} |")
    lines += ['', '基线回复的离线解包只验证接口能否处理已归档文本；没有执行修复后的算例，不能当作复测或新增保留样本。候选归档正文与解码结果一致时，才是实际写入路径的证据。所有逐文件路径、哈希及致命日志行号见 [JSON](content-observations.json)。', '',
              '执行退出码和 End 标记不能独立证明后处理成功；FOAM FATAL 日志、必要参考输出和原任务验收需要一起核对。证据缺失仍按主报告保留。', '']
    (batch / 'content-observations.md').write_text('\n'.join(lines))
    print(json.dumps(groups, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
