import streamlit as st
import io
import re
import pandas as pd
from pdfminer.high_level import extract_text

st.set_page_config(page_title="Advanced RN Resume Processor", layout="wide")
st.title("Advanced RN Candidate Offline Resume Tool")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\-\(\) \.]{7,}\d")

def extract_pdf(pdf_bytes):
    result = {"text": "", "emails": [], "name": None}
    try:
        text = extract_text(io.BytesIO(pdf_bytes)) or ""
        result['text'] = text
        result['emails'] = list(dict.fromkeys(EMAIL_RE.findall(text)))
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in lines[:8]:
            if 1 < len(ln.split()) <= 5:
                result['name'] = ln
                break
    except:
        pass
    return result

left, right = st.columns([3, 1])
with left:
    uploaded = st.file_uploader("Upload PDFs", type=['pdf'])
    keywords = st.text_input("Keywords", value="RN LPN")
    if st.button("Parse"):
        if uploaded:
            data = uploaded.read()
            parsed = extract_pdf(data)
            st.write(f"Name: {parsed['name']}")
            st.write(f"Emails: {', '.join(parsed['emails'])}")
            st.text_area("Resume", parsed['text'], height=400)
        else:
            st.error("Please upload a file")
with right:
    st.markdown("**Info**")
    st.info("Upload PDF resumes to extract text")
