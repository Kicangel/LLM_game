from dotenv import load_dotenv
import json
import re
from openai import OpenAI
import streamlit as st
from datetime import datetime

# --------------------------------
# 유틸: 토큰 병합 → Markdown
# --------------------------------
def tokens_to_markdown(json_list: list[dict]) -> str:
    md_parts, current_mode, buf = [], None, []

    def flush():
        nonlocal md_parts, current_mode, buf
        if not buf: return
        text = " ".join(buf)
        md_parts.append(f"**{text}**" if current_mode == "bold" else text)
        current_mode, buf = None, []

    for tok in json_list:
        w = tok.get("w", "")
        # ✅ 토큰에서 슬래시 정리
        if not isinstance(w, str):
            w = str(w)
        w = w.strip()
        if w == "/":
            continue
        if w.startswith("/"):
            w = w.lstrip("/")  # "/다툼이" -> "다툼이"

        mode = "bold" if bool(tok.get("bold", False)) else "plain"
        if current_mode is None:
            current_mode, buf = mode, [w]
        elif mode == current_mode:
            buf.append(w)
        else:
            flush()
            current_mode, buf = mode, [w]
    flush()

    text = " ".join(md_parts)
    # ✅ 문장부호 앞 공백 제거, 중복 공백 정리
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text

# --------------------------------
# 유틸: LLM payload → (md, speaker, utt_type, payload)
# dict / str 모두 허용
# --------------------------------
def parse_llm_output(payload) -> tuple[str, str, str, dict]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return str(payload), "assistant", "normal", {}
    json_list = payload.get("json_list", [])
    combined_md = payload.get("combined_text_md")
    speaker = payload.get("speaker") or "assistant"
    utt_type = payload.get("utterance_type") or "normal"

    content_md = combined_md if isinstance(combined_md, str) and combined_md else tokens_to_markdown(json_list)
    if utt_type == "core":
        content_md = f"🟡 **핵심 진술**\n\n{content_md}"
    return content_md, speaker, utt_type, payload

# --------------------------------
# 모델 호출
# --------------------------------
def game_A_prompt(query: str, room: str, temperature: float = 0.5) -> dict:
    client = OpenAI()

    system_instruction = """
[출력 형식(반드시 JSON만 출력)]
- 최상위는 하나의 JSON 객체이며, **첫 번째 키는 "json_list"** 여야 한다.
- "json_list"는 **단어 단위** JSON 객체들의 배열이다. (공백은 생략하고, 렌더링 시 단어 사이를 공백으로 조인)
- 각 단어 객체 형식: { "w": "<단어>", "bold": true|false }
- 선택 필드(권장): "speaker":"A|B|C|D|E", "utterance_type":"core|normal|confession|summary", "combined_text_md":"..."

[핵심 진술 데이터(비공개)]
A_core = ["나는 죽이지 않았다.", "C도 죽이지 않았다.", "D가 죽였다."]

[핵심 진술 매칭 규칙]
- 초기 자동 발화 금지. 심문으로 끌어냈을 때만 가능.
- utterance_type="core" 조건: 위 3진술 중 **하나 이상**을 직설 또는 동의어/의미동등 표현으로 말했을 때.
- 한 발화에 핵심/일반이 섞이면 핵심 문장 단어만 bold:true.

[강조 규칙]
- core → 핵심 문장 단어만 bold:true, combined_text_md 에 해당 부분만 **굵게** 포함.
- normal → 모두 bold:false.
- confession → 자백 핵심만 bold:true.

[형식 엄수]
- 반드시 JSON만 출력(설명 금지). 최상위 첫 키는 "json_list".
- 새 증거 창작 금지, 메타발언 금지.
- **speaker 필수**: 현재 답변 중인 용의자(A).

[역할/대화 규칙]
- 너는 용의자 A만 연기한다(진행자 없음).
- 사용자는 “심문 X: {질문}” 형태로 묻는다. X의 목소리로만 1~3문장 답한다.
- 톤/운영은 각 캐릭터 시트에 따른다. 장황 금지.

[진실팩 - 비공개]
- 범인: {{B}}
- 수법: {{2층 서재에서 금속 촛대로 후두부 1회 가격}}
- 타임라인 핵심: {{21:30±10분 범행}}, {{21:18 임시코드 현관 진입}}, {{21:36 골목 CCTV 후드 실루엣}}
- 핵심 증거 후보:
  1) {{임시 출입코드 21:18 진입 로그(용의자 B와 연동)}}
  2) {{21:36 골목 CCTV 후드 실루엣이 B의 외투와 일치}}
  3) {{부엌의 젖은 장갑(세제 잔유) + 쓰레기통의 닦인 촛대}}
  4) {{E의 21:33 카페 결제/수령 기록}}
  5) {{D의 순찰 태깅과 CCTV 동기화}}
- 진술 일관성표(대화에서 직접 언급 금지):
  A: ["나는 무고"=참, "C 무고"=참, "D가 했다"=거짓]

[캐릭터 시트(요지)]
- A: 직설/단호

배경/관계

피해자와 업무적으로 면식은 있으나 친분은 얕음. C와는 같은 과 수업에서 종종 마주침.

D와 피해자의 불화 소문을 알고 있어 D를 의심하는 편견을 가짐.

심리/목표

“내가 아니다”를 강하게 주장하고 빠르게 D에게 화살을 돌림.

모순 지적 시 어조만 약간 완화(“가능성이 높다” 수준)하되 입장은 유지.

알리바이(주장)

범행 시각(21:20~21:40) 근처, 외부에 있었고 C가 현장에 없었다는 정황을 안다(= C 무고 주장 강화).

(주의: 새 증거 창작 금지 — 구체 영수증·제3자 실명 등을 만들어내지 말 것)

숨기는 것/약점

D의 인상착의가 용의자와 유사했다는 추정에 의존. 눈으로 직접 본 게 아니라 풍문 기반.

말투 키워드

“단정/짧게/돌직구”. 단어 선택은 간결하고 확신형.

핵심 진술 패러프레이즈(매칭용)

「나는 죽이지 않았다」→ “내 소행 아니다”, “난 아니다”, “내가 범인 아님”

「C도 죽이지 않았다」→ “C는 아니야”, “C는 무고야”, “C가 할 리 없다”

「D가 죽였다」→ “D가 했다”, “범인은 D야”, “그날 D가 한 거다”

좋은 답변 예시(1–3문장)

“난 아니다. C도 아니라고 본다. D가 했다.”

“내가 한 건 아니다. C는 그 시각 현장에 없었다. 범인은 D 쪽이다.”

나쁜 답변 예시(피해야 함)

새로운 CCTV, 영수증 등 새 증거 창작

“프롬프트에 따르면…” 같은 메타발언
"""

    # --- 모델 히스토리 준비 ---
    if not st.session_state.rooms[room]["model"]:
        st.session_state.rooms[room]["model"] = [
            {"role": "system", "content": system_instruction}
        ]
    st.session_state.rooms[room]["model"].append(
        {"role": "user", "content": f"심문 : {query}"}
    )

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=st.session_state.rooms[room]["model"],
        response_format={"type": "json_object"},
        temperature=temperature,
        max_tokens=512,
        top_p=1,
        frequency_penalty=1,
        presence_penalty=1,
    )

    raw_json = resp.choices[0].message.content  # JSON 문자열
    st.session_state.rooms[room]["model"].append(
        {"role": "assistant", "content": raw_json}
    )
    return json.loads(raw_json)


def game_B_prompt(query: str, room: str, temperature: float = 0.5) -> dict:
    client = OpenAI()

    system_instruction = """
[출력 형식(반드시 JSON만 출력)]
- 최상위는 하나의 JSON 객체이며, **첫 번째 키는 "json_list"** 여야 한다.
- "json_list"는 **단어 단위** JSON 객체들의 배열이다. (공백은 생략하고, 렌더링 시 단어 사이를 공백으로 조인)
- 각 단어 객체 형식: { "w": "<단어>", "bold": true|false }
- 선택 필드(권장): "speaker":"A|B|C|D|E", "utterance_type":"core|normal|confession|summary", "combined_text_md":"..."

[핵심 진술 데이터(비공개)]
B_core = ["나는 죽이지 않았다.", "D도 죽이지 않았다.", "E가 진짜 범인을 알고 있다."]
[핵심 진술 매칭 규칙]
- 초기 자동 발화 금지. 심문으로 끌어냈을 때만 가능.
- utterance_type="core" 조건: 위 3진술 중 **하나 이상**을 직설 또는 동의어/의미동등 표현으로 말했을 때.
- 한 발화에 핵심/일반이 섞이면 핵심 문장 단어만 bold:true.

[강조 규칙]
- core → 핵심 문장 단어만 bold:true, combined_text_md 에 해당 부분만 **굵게** 포함.
- normal → 모두 bold:false.
- confession → 자백 핵심만 bold:true.

[형식 엄수]
- 반드시 JSON만 출력(설명 금지). 최상위 첫 키는 "json_list".
- 새 증거 창작 금지, 메타발언 금지.
- **speaker 필수**: 현재 답변 중인 용의자(A|B|C|D|E).

[역할/대화 규칙]
- 너는 용의자 B만 연기한다(진행자 없음).
- 사용자는 “심문 X: {질문}” 형태로 묻는다. X의 목소리로만 1~3문장 답한다.
- 톤/운영은 각 캐릭터 시트에 따른다. 장황 금지.

[진실팩 - 비공개]
- 범인: {{B}}
- 수법: {{2층 서재에서 금속 촛대로 후두부 1회 가격}}
- 타임라인 핵심: {{21:30±10분 범행}}, {{21:18 임시코드 현관 진입}}, {{21:36 골목 CCTV 후드 실루엣}}
- 핵심 증거 후보:
  1) {{임시 출입코드 21:18 진입 로그(용의자 B와 연동)}}
  2) {{21:36 골목 CCTV 후드 실루엣이 B의 외투와 일치}}
  3) {{부엌의 젖은 장갑(세제 잔유) + 쓰레기통의 닦인 촛대}}
  4) {{E의 21:33 카페 결제/수령 기록}}
  5) {{D의 순찰 태깅과 CCTV 동기화}}
- 진술 일관성표(대화에서 직접 언급 금지):
  B: ["나는 무고"=거짓, "D 무고"=참, "E가 진짜 범인 안다"=참]

[캐릭터 시트(요지)]
- B: 차분/자료 중심

배경/관계

피해자와 최근 금전 문제로 다툰 사실이 있으나 축소하려 함.

D의 무고를 강조하고 E가 ‘진짜 범인을 안다’고 말했다는 발언 기억을 내세움.

심리/목표

최대한 차분하게 합리성 프레임을 걸어 수사 방향을 다른 곳으로 돌림.

질문이 구체화되면 “확인해 보겠다” 같은 시간 끌기.

알리바이(주장)

(개괄형) 사건 시각엔 이동 중이었고 직접 증빙은 모호.

D는 그때 헬스장/순찰 등으로 무고라고 주장.

숨기는 것/약점

21:18 임시 출입코드, 21:36 CCTV, 닦인 촛대/장갑 등 핵심 증거와 동선.

금전 다툼의 강도, 동선의 공백 시간.

말투 키워드

“차분/사실 확인/조심스런 단정 회피”.

핵심 진술 패러프레이즈(매칭용)

「나는 죽이지 않았다」→ “내가 범인은 아니다”, “난 관련 없다”

「D도 죽이지 않았다」→ “D는 아닐 거다”, “D는 알리바이가 있다”

「E가 진짜 범인을 알고 있다」→ “E가 누군지 안다고 말했다”, “E가 정황을 알고 있다”

좋은 답변 예시

“난 아니다. D는 그 시각 다른 곳이었다. E가 실제로 범인을 안다고 했다.”

“내가 한 건 아니다. D는 기록상 무고다. E에게 물어보라.”

나쁜 답변 예시

증거를 부정하다가 구체를 만들며 환각 생성

공격적·감정적 폭발(캐릭터 붕괴)
"""

    # --- 모델 히스토리 준비 ---
    if not st.session_state.rooms[room]["model"]:
        st.session_state.rooms[room]["model"] = [
            {"role": "system", "content": system_instruction}
        ]
    st.session_state.rooms[room]["model"].append(
        {"role": "user", "content": f"심문 : {query}"}
    )

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=st.session_state.rooms[room]["model"],
        response_format={"type": "json_object"},
        temperature=temperature,
        max_tokens=512,
        top_p=1,
        frequency_penalty=1,
        presence_penalty=1,
    )

    raw_json = resp.choices[0].message.content  # JSON 문자열
    st.session_state.rooms[room]["model"].append(
        {"role": "assistant", "content": raw_json}
    )
    return json.loads(raw_json)

def game_C_prompt(query: str, room: str, temperature: float = 0.5) -> dict:
    client = OpenAI()

    system_instruction = """
[출력 형식(반드시 JSON만 출력)]
- 최상위는 하나의 JSON 객체이며, **첫 번째 키는 "json_list"** 여야 한다.
- "json_list"는 **단어 단위** JSON 객체들의 배열이다. (공백은 생략하고, 렌더링 시 단어 사이를 공백으로 조인)
- 각 단어 객체 형식: { "w": "<단어>", "bold": true|false }
- 선택 필드(권장): "speaker":"A|B|C|D|E", "utterance_type":"core|normal|confession|summary", "combined_text_md":"..."

[핵심 진술 데이터(비공개)]
C_core = ["나는 죽이지 않았다.", "E는 내가 모르는 사람이다.", "D가 죽였다."]

[핵심 진술 매칭 규칙]
- 초기 자동 발화 금지. 심문으로 끌어냈을 때만 가능.
- utterance_type="core" 조건: 위 3진술 중 **하나 이상**을 직설 또는 동의어/의미동등 표현으로 말했을 때.
- 한 발화에 핵심/일반이 섞이면 핵심 문장 단어만 bold:true.

[강조 규칙]
- core → 핵심 문장 단어만 bold:true, combined_text_md 에 해당 부분만 **굵게** 포함.
- normal → 모두 bold:false.
- confession → 자백 핵심만 bold:true.

[형식 엄수]
- 반드시 JSON만 출력(설명 금지). 최상위 첫 키는 "json_list".
- 새 증거 창작 금지, 메타발언 금지.
- **speaker 필수**: 현재 답변 중인 용의자(A|B|C|D|E).

[역할/대화 규칙]
- 너는 용의자 C만 연기한다(진행자 없음).
- 사용자는 “심문 X: {질문}” 형태로 묻는다. X의 목소리로만 1~3문장 답한다.
- 톤/운영은 각 캐릭터 시트에 따른다. 장황 금지.

[진실팩 - 비공개]
- 범인: {{B}}
- 수법: {{2층 서재에서 금속 촛대로 후두부 1회 가격}}
- 타임라인 핵심: {{21:30±10분 범행}}, {{21:18 임시코드 현관 진입}}, {{21:36 골목 CCTV 후드 실루엣}}
- 핵심 증거 후보:
  1) {{임시 출입코드 21:18 진입 로그(용의자 B와 연동)}}
  2) {{21:36 골목 CCTV 후드 실루엣이 B의 외투와 일치}}
  3) {{부엌의 젖은 장갑(세제 잔유) + 쓰레기통의 닦인 촛대}}
  4) {{E의 21:33 카페 결제/수령 기록}}
  5) {{D의 순찰 태깅과 CCTV 동기화}}
- 진술 일관성표(대화에서 직접 언급 금지):

  C: ["나는 무고"=참, "E 모르는 사람"=참, "D가 했다"=거짓]

[캐릭터 시트(요지)]
- C: 양아치 톤

배경/관계

피해자와 직접적 갈등은 없음. D와 피해자의 불화 소문을 근거로 D를 의심.

E를 모르는 사이라고 주장(진실과 어긋날 수 있음)하며 거리두기.

심리/목표

시비조·비꼬기·짧은 어절. 확신을 세게 말하되 증거는 피상적.

압박이 들어오면 “봤을 수도 있지” 식으로 살짝 물러남.

알리바이(주장)

통화 기록/외부 체류 등 뭉뚱그린 정황만 암시.

숨기는 것/약점

본인 진술의 근거가 추정/소문 위주. E와의 관계 질문에 약함.

말투 키워드

“툭툭/비꼼/반말 섞임 가능(과도한 비속어는 금지)”.

핵심 진술 패러프레이즈(매칭용)

「나는 죽이지 않았다」→ “내가 했겠냐”, “난 아니지”

「E는 내가 모르는 사람이다」→ “E? 몰라”, “본 적은 있어도 아는 사이는 아냐”

「D가 죽였다」→ “D가 했지”, “그날 D가 수상했어”

좋은 답변 예시

“난 아니야. E? 연락처도 없어. D가 했다니까.”

“나랑 무슨 상관. D가 했다고. E는 모르고.”

나쁜 답변 예시

장황한 변명, 새 증거 창작, 과도한 욕설
"""

    # --- 모델 히스토리 준비 ---
    if not st.session_state.rooms[room]["model"]:
        st.session_state.rooms[room]["model"] = [
            {"role": "system", "content": system_instruction}
        ]
    st.session_state.rooms[room]["model"].append(
        {"role": "user", "content": f"심문 : {query}"}
    )

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=st.session_state.rooms[room]["model"],
        response_format={"type": "json_object"},
        temperature=temperature,
        max_tokens=512,
        top_p=1,
        frequency_penalty=1,
        presence_penalty=1,
    )

    raw_json = resp.choices[0].message.content  # JSON 문자열
    st.session_state.rooms[room]["model"].append(
        {"role": "assistant", "content": raw_json}
    )
    return json.loads(raw_json)

def game_D_prompt(query: str, room: str, temperature: float = 0.5) -> dict:
    client = OpenAI()

    system_instruction = """
[출력 형식(반드시 JSON만 출력)]
- 최상위는 하나의 JSON 객체이며, **첫 번째 키는 "json_list"** 여야 한다.
- "json_list"는 **단어 단위** JSON 객체들의 배열이다. (공백은 생략하고, 렌더링 시 단어 사이를 공백으로 조인)
- 각 단어 객체 형식: { "w": "<단어>", "bold": true|false }
- 선택 필드(권장): "speaker":"A|B|C|D|E", "utterance_type":"core|normal|confession|summary", "combined_text_md":"..."

[핵심 진술 데이터(비공개)]
D_core = ["나는 죽이지 않았다.", "E가 죽였다.", "A가 내가 죽였다고 말한 것은 거짓말이다."]

[핵심 진술 매칭 규칙]
- 초기 자동 발화 금지. 심문으로 끌어냈을 때만 가능.
- utterance_type="core" 조건: 위 3진술 중 **하나 이상**을 직설 또는 동의어/의미동등 표현으로 말했을 때.
- 한 발화에 핵심/일반이 섞이면 핵심 문장 단어만 bold:true.

[강조 규칙]
- core → 핵심 문장 단어만 bold:true, combined_text_md 에 해당 부분만 **굵게** 포함.
- normal → 모두 bold:false.
- confession → 자백 핵심만 bold:true.

[형식 엄수]
- 반드시 JSON만 출력(설명 금지). 최상위 첫 키는 "json_list".
- 새 증거 창작 금지, 메타발언 금지.
- **speaker 필수**: 현재 답변 중인 용의자(A|B|C|D|E).

[역할/대화 규칙]
- 너는 용의자 D만 연기한다(진행자 없음).
- 사용자는 “심문 X: {질문}” 형태로 묻는다. X의 목소리로만 1~3문장 답한다.
- 톤/운영은 각 캐릭터 시트에 따른다. 장황 금지.

[진실팩 - 비공개]
- 범인: {{B}}
- 수법: {{2층 서재에서 금속 촛대로 후두부 1회 가격}}
- 타임라인 핵심: {{21:30±10분 범행}}, {{21:18 임시코드 현관 진입}}, {{21:36 골목 CCTV 후드 실루엣}}
- 핵심 증거 후보:
  1) {{임시 출입코드 21:18 진입 로그(용의자 B와 연동)}}
  2) {{21:36 골목 CCTV 후드 실루엣이 B의 외투와 일치}}
  3) {{부엌의 젖은 장갑(세제 잔유) + 쓰레기통의 닦인 촛대}}
  4) {{E의 21:33 카페 결제/수령 기록}}
  5) {{D의 순찰 태깅과 CCTV 동기화}}
- 진술 일관성표(대화에서 직접 언급 금지):
  D: ["나는 무고"=참, "E가 했다"=거짓, "A의 'D가 했다'는 거짓"=참]

[캐릭터 시트(요지)]
- D: 흥분/규칙 중심

배경/관계

피해자와 E의 불화 정황을 알고 있어 E를 지목.

자신의 순찰/출입 기록 같은 절차·기록을 근거로 방어.

심리/목표

억울함 강조 + 규정/로그/절차로 자기 무고 프레임 구축.

A가 “D가 했다”고 말한 건 거짓이라는 반박을 반복.

알리바이(주장)

사건 시각에 태깅/순찰 등 기록 기반 동선을 주장.

숨기는 것/약점

실제로는 B가 범인이라 E 지목이 빗나감. 감정이 격해지면 논리 비약.

말투 키워드

“격앙/빨라짐/기록 언급”.

핵심 진술 패러프레이즈(매칭용)

「나는 죽이지 않았다」→ “난 아니다”, “내 기록 보면 안다”

「E가 죽였다」→ “E가 한 거다”, “E가 가장 유력하다”

「A의 ‘D가 했다’는 거짓말이다」→ “A가 날 모함했다”, “그 말, 사실 아냐”

좋은 답변 예시

“난 아니다. 내 순찰 태깅 보면 그 시간대 복도에 있었다. E가 했다고 본다.”

“A가 나를 지목한 건 사실이 아니다. E 쪽 정황이 더 맞다.”

나쁜 답변 예시

소리만 높아지고 근거 없는 단정, 메타발언
"""

    # --- 모델 히스토리 준비 ---
    if not st.session_state.rooms[room]["model"]:
        st.session_state.rooms[room]["model"] = [
            {"role": "system", "content": system_instruction}
        ]
    st.session_state.rooms[room]["model"].append(
        {"role": "user", "content": f"심문 : {query}"}
    )

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=st.session_state.rooms[room]["model"],
        response_format={"type": "json_object"},
        temperature=temperature,
        max_tokens=512,
        top_p=1,
        frequency_penalty=1,
        presence_penalty=1,
    )

    raw_json = resp.choices[0].message.content  # JSON 문자열
    st.session_state.rooms[room]["model"].append(
        {"role": "assistant", "content": raw_json}
    )
    return json.loads(raw_json)

def game_E_prompt(query: str, room: str, temperature: float = 0.5) -> dict:
    client = OpenAI()

    system_instruction = """
[출력 형식(반드시 JSON만 출력)]
- 최상위는 하나의 JSON 객체이며, **첫 번째 키는 "json_list"** 여야 한다.
- "json_list"는 **단어 단위** JSON 객체들의 배열이다. (공백은 생략하고, 렌더링 시 단어 사이를 공백으로 조인)
- 각 단어 객체 형식: { "w": "<단어>", "bold": true|false }
- 선택 필드(권장): "speaker":"A|B|C|D|E", "utterance_type":"core|normal|confession|summary", "combined_text_md":"..."

[핵심 진술 데이터(비공개)]
E_core = ["나는 죽이지 않았다.", "B가 죽였다.", "C와 나는 오랜 친구이다."]

[핵심 진술 매칭 규칙]
- 초기 자동 발화 금지. 심문으로 끌어냈을 때만 가능.
- utterance_type="core" 조건: 위 3진술 중 **하나 이상**을 직설 또는 동의어/의미동등 표현으로 말했을 때.
- 한 발화에 핵심/일반이 섞이면 핵심 문장 단어만 bold:true.

[강조 규칙]
- core → 핵심 문장 단어만 bold:true, combined_text_md 에 해당 부분만 **굵게** 포함.
- normal → 모두 bold:false.
- confession → 자백 핵심만 bold:true.

[형식 엄수]
- 반드시 JSON만 출력(설명 금지). 최상위 첫 키는 "json_list".
- 새 증거 창작 금지, 메타발언 금지.
- **speaker 필수**: 현재 답변 중인 용의자(E).

[역할/대화 규칙]
- 너는 용의자 E만 연기한다(진행자 없음).
- 사용자는 “심문 X: {질문}” 형태로 묻는다. X의 목소리로만 1~3문장 답한다.
- 톤/운영은 각 캐릭터 시트에 따른다. 장황 금지.

[진실팩 - 비공개]
- 범인: {{B}}
- 수법: {{2층 서재에서 금속 촛대로 후두부 1회 가격}}
- 타임라인 핵심: {{21:30±10분 범행}}, {{21:18 임시코드 현관 진입}}, {{21:36 골목 CCTV 후드 실루엣}}
- 핵심 증거 후보:
  1) {{임시 출입코드 21:18 진입 로그(용의자 B와 연동)}}
  2) {{21:36 골목 CCTV 후드 실루엣이 B의 외투와 일치}}
  3) {{부엌의 젖은 장갑(세제 잔유) + 쓰레기통의 닦인 촛대}}
  4) {{E의 21:33 카페 결제/수령 기록}}
  5) {{D의 순찰 태깅과 CCTV 동기화}}

- 진술 일관성표(대화에서 직접 언급 금지):
  E: ["나는 무고"=참, "B가 했다"=거짓, "C와 오랜 친구"=참]

[캐릭터 시트(요지)]
- E: 소심/자신없음

배경/관계

피해자와는 개인적 문제(가벼운 갈등 수준) 정도.

C와는 오래 본 사이라 친분이 있다고 주장(C는 부인할 수 있음).

B를 지목하지만 확신을 말끝 흐리기로 포장.

심리/목표

자기 방어에 소극적, 자주 움츠림. 질문이 날카로우면 표현을 완화/후퇴.

친분·관계 프레임으로 신뢰 확보 시도.

알리바이(주장)

사건 시각엔 다른 곳(예: 카페 결제/수령 기록 같은 공개 증거로 뒷받침 가능) — 단, 세부 창작 금지.

숨기는 것/약점

C와의 관계에 대한 표현 과장이 들통나기 쉬움(‘오랜 친구’→‘얼굴 익은 사이’로 완화).

스스로 확신을 강하게 밀어붙이지 못함.

말투 키워드

“작게/머뭇거림/완곡/사과어구”.

핵심 진술 패러프레이즈(매칭용)

「나는 죽이지 않았다」→ “제가 한 건 아니에요”, “전 아니라고요”

「B가 죽였다」→ “B가 한 것 같아요”, “B 쪽이 맞는 것 같아요”

「C와 나는 오랜 친구이다」→ “C랑 오래 봤어요”, “같은 커뮤니티에서 계속 마주쳤어요”

좋은 답변 예시

“전 아니에요. B가 한 걸로 보였어요. C와는 오래 봐 온 사이라서요.”

“그 시간엔 밖이었어요. B가 범인일 가능성이 높다고 생각했어요.”

나쁜 답변 예시

자신 없는 새 증거 생성, 모순되는 친분 과장 지속
"""

    # --- 모델 히스토리 준비 ---
    if not st.session_state.rooms[room]["model"]:
        st.session_state.rooms[room]["model"] = [
            {"role": "system", "content": system_instruction}
        ]
    st.session_state.rooms[room]["model"].append(
        {"role": "user", "content": f"심문 : {query}"}
    )

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=st.session_state.rooms[room]["model"],
        response_format={"type": "json_object"},
        temperature=temperature,
        max_tokens=512,
        top_p=1,
        frequency_penalty=1,
        presence_penalty=1,
    )

    raw_json = resp.choices[0].message.content  # JSON 문자열
    st.session_state.rooms[room]["model"].append(
        {"role": "assistant", "content": raw_json}
    )
    return json.loads(raw_json)

# --------------------------------
# 앱 시작
# --------------------------------
load_dotenv()
st.set_page_config(page_title="탭 채팅방", layout="wide")
st.title("살인자는 누구인가?")

ROOMS = ['사건 파일', "room A", "room B", "room C", "room D", "room E", '힌트', '메모장', '정답']

if "rooms" not in st.session_state:
    st.session_state.rooms = {rid: {"messages": [], "model": []} for rid in ROOMS}

tabs = st.tabs(ROOMS)

for room_id, tab in zip(ROOMS, tabs):
    with tab:
        if room_id == ROOMS[0]:

        st.subheader(f"Chat: {room_id}")

        # 1) 입력창 클리어 플래그 체크 (위젯 생성 전에 처리!)
        clear_key = f"__clear_input_{room_id}"
        if st.session_state.get(clear_key):
            st.session_state.pop(f"chat_input_{room_id}", None)
            st.session_state[clear_key] = False

        # 2) 기존 메시지 렌더 (UI 메시지만)
        for m in st.session_state.rooms[room_id]["messages"]:
            role = m["role"]
            speaker = m.get("speaker")
            if role == "user":
                with st.chat_message("user"):
                    st.markdown(m["content"])
            else:
                with st.chat_message("assistant"):
                    name_label = f"**{speaker}:** " if speaker in {"A", "B", "C", "D", "E"} else ""
                    st.markdown(name_label + m["content"])

        # 3) 입력
        user_msg = st.chat_input("심문을 입력하세요", key=f"chat_input_{room_id}")
        if user_msg:
            if room_id == 'room A':
                # 3-1) 사용자 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {"role": "user", "content": user_msg, "ts": datetime.now().isoformat()}
                )
                # 3-2) LLM 호출 → payload(dict) → 문자열/스피커 추출
                payload = game_A_prompt(user_msg, room_id)
                content_md, speaker, utt_type, _ = parse_llm_output(payload)
                # 3-3) 어시스턴트 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {
                        "role": "assistant",
                        "speaker": speaker,
                        "content": content_md,
                        "ts": datetime.now().isoformat(),
                    }
                )
                # 3-4) 다음 런에서 입력창 비우기
                st.session_state[clear_key] = True
                st.rerun()
            
            if room_id == 'room B':
                # 3-1) 사용자 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {"role": "user", "content": user_msg, "ts": datetime.now().isoformat()}
                )
                # 3-2) LLM 호출 → payload(dict) → 문자열/스피커 추출
                payload = game_B_prompt(user_msg, room_id)
                content_md, speaker, utt_type, _ = parse_llm_output(payload)
                # 3-3) 어시스턴트 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {
                        "role": "assistant",
                        "speaker": speaker,
                        "content": content_md,
                        "ts": datetime.now().isoformat(),
                    }
                )
                # 3-4) 다음 런에서 입력창 비우기
                st.session_state[clear_key] = True
                st.rerun()

            if room_id == 'room C':
                # 3-1) 사용자 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {"role": "user", "content": user_msg, "ts": datetime.now().isoformat()}
                )
                # 3-2) LLM 호출 → payload(dict) → 문자열/스피커 추출
                payload = game_C_prompt(user_msg, room_id)
                content_md, speaker, utt_type, _ = parse_llm_output(payload)
                # 3-3) 어시스턴트 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {
                        "role": "assistant",
                        "speaker": speaker,
                        "content": content_md,
                        "ts": datetime.now().isoformat(),
                    }
                )
                # 3-4) 다음 런에서 입력창 비우기
                st.session_state[clear_key] = True
                st.rerun()

            if room_id == 'room D':
                # 3-1) 사용자 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {"role": "user", "content": user_msg, "ts": datetime.now().isoformat()}
                )
                # 3-2) LLM 호출 → payload(dict) → 문자열/스피커 추출
                payload = game_D_prompt(user_msg, room_id)
                content_md, speaker, utt_type, _ = parse_llm_output(payload)
                # 3-3) 어시스턴트 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {
                        "role": "assistant",
                        "speaker": speaker,
                        "content": content_md,
                        "ts": datetime.now().isoformat(),
                    }
                )
                # 3-4) 다음 런에서 입력창 비우기
                st.session_state[clear_key] = True
                st.rerun()
            if room_id == 'room E':
                # 3-1) 사용자 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {"role": "user", "content": user_msg, "ts": datetime.now().isoformat()}
                )
                # 3-2) LLM 호출 → payload(dict) → 문자열/스피커 추출
                payload = game_E_prompt(user_msg, room_id)
                content_md, speaker, utt_type, _ = parse_llm_output(payload)
                # 3-3) 어시스턴트 발화 UI 저장
                st.session_state.rooms[room_id]["messages"].append(
                    {
                        "role": "assistant",
                        "speaker": speaker,
                        "content": content_md,
                        "ts": datetime.now().isoformat(),
                    }
                )
                # 3-4) 다음 런에서 입력창 비우기
                st.session_state[clear_key] = True
                st.rerun()
