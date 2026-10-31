시나리오 A 분석 코드 (제안값 포함, 근거는 guidelines/scenarioA_self_approval_report.md)
- plan_model.py: 가격기준.xlsx의 복종별 원가에서 시즌 발주액 계산(값은 파일에 옮겨 적음)
- sweep.py / robust.py: 스타일 수 조합과 수요 배수 민감도
- expo_sweep.py: 구색-수요 연결(D형) 민감도, 결과 expo_results.json
실행: cd analysis && python3 expo_sweep.py   (몇 분 걸림)
