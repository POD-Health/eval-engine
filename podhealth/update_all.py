from openpyxl import load_workbook
import json

file_path = "src/docs/testfile.xlsx"
result_path = "podhealth/result.json"

wb = load_workbook(file_path)
ws = wb.active

with open(result_path, "r", encoding="utf-8") as f:
    results = json.load(f)


result_map = {}
for item in results:
    qid = str(item.get("question_id", "")).strip()
    if qid:
        result_map[qid] = item

for row in range(5, ws.max_row + 1):
    qid = ws.cell(row=row, column=2).value  

    if not qid:
        continue

    qid = str(qid).strip()
    matched_item = result_map.get(qid)

    if not matched_item:
        continue

    actual_response = matched_item.get("response", "")
    score = matched_item.get("score", 0)   
    reason = matched_item.get("reason", "")
    response_date = matched_item.get("response_date", "")
    model_version = matched_item.get("model_version", "Piper AI Agent")

   
    accuracy = f"{score * 10}%"

  
    if score >= 7:
        pass_fail = "Pass"
        retest = "No"
        priority = "Low"
        tester_notes = "Response matches expected answer well"
    elif score >= 5:
        pass_fail = "Partial"
        retest = "Yes"
        priority = "Medium"
        tester_notes = "Response partially matches expected answer"
    else:
        pass_fail = "Fail"
        retest = "Yes"
        priority = "High"
        tester_notes = "Response missed important expected points"

    missing_wrong_info = reason if reason else "Review expected answer vs actual response"

    
    ws.cell(row=row, column=6).value = actual_response       # F = Actual Response
    ws.cell(row=row, column=7).value = response_date         # G = Response Date
    ws.cell(row=row, column=8).value = model_version         # H = Model Version
    ws.cell(row=row, column=9).value = score                # I = Score (0-10)
    ws.cell(row=row, column=10).value = accuracy            # J = Accuracy %
    ws.cell(row=row, column=11).value = pass_fail           # K = Pass/Fail
    ws.cell(row=row, column=12).value = missing_wrong_info  # L = Missing/Wrong Info
    ws.cell(row=row, column=13).value = tester_notes        # M = Tester Notes
    ws.cell(row=row, column=14).value = retest              # N = Retest Required
    ws.cell(row=row, column=15).value = priority            

wb.save(file_path)
print("Excel updated successfully")