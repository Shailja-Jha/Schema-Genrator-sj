import streamlit as st
import os
import graphviz
import json
import re
from typing import Dict
from langchain_core.prompts import PromptTemplate  # Updated import
from langchain_community.llms import HuggingFaceHub

from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Set the token from the loaded environment variable
os.environ["HUGGINGFACEHUB_API_TOKEN"] = os.getenv("HUGGINGFACEHUB_API_TOKEN")


# Initialize Hugging Face model through LangChain
llm = HuggingFaceHub(
    repo_id="mistralai/Mixtral-8x7B-Instruct-v0.1",
    model_kwargs={"temperature": 0.3, "max_length": 1000}
)

# Corrected PromptTemplate with proper imports
schema_prompt = PromptTemplate(
    input_variables=["description", "schema_type"],
    template="""
You are a database design expert. Generate a detailed database schema in JSON format ONLY based on:
- Application description: {description}
- Schema type: {schema_type}

Output MUST be a SINGLE VALID JSON object with this EXACT structure:
{{
    "schema_type": "relational|nosql",
    "tables|collections": [
        {{
            "name": "table_name",
            "fields": [
                {{
                    "name": "field_name",
                    "type": "data_type",
                    "constraints": ["constraint1", "constraint2"]
                }}
            ],
            "relationships": [
                {{
                    "type": "1:1|1:N|M:N",
                    "related_to": "related_table",
                    "field": "foreign_key_field"
                }}
            ]
        }}
    ],
    "explanation": "Brief design explanation"
}}

IMPORTANT RULES:
1. Output ONLY the raw JSON with NO additional text
2. Ensure all quotes are straight double quotes (")
3. No trailing commas in arrays/objects
4. No comments or explanations outside the JSON
5. The "explanation" field must be the last field
6. All brackets and braces must be properly closed

BEGIN OUTPUT:
"""
)

def extract_json_from_response(response: str) -> Dict:
    """Robust JSON extraction with multiple fallback strategies"""
    cleaned = response.strip()
    
    # Strategy 1: Try parsing the entire response as-is
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    
    # Strategy 2: Look for JSON in markdown code blocks
    code_blocks = re.findall(r'```(?:json)?\n(.*?)\n```', cleaned, re.DOTALL)
    for block in code_blocks:
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            continue
    
    # Strategy 3: Find the first { and last } and try to parse what's between them
    start_idx = cleaned.find('{')
    end_idx = cleaned.rfind('}')
    
    if start_idx != -1 and end_idx != -1:
        potential_json = cleaned[start_idx:end_idx+1]
        try:
            return json.loads(potential_json)
        except json.JSONDecodeError as e:
            # Strategy 4: Try to fix common JSON issues
            try:
                fixed = re.sub(r',\s*([}\]])', r'\1', potential_json)
                return json.loads(fixed)
            except json.JSONDecodeError:
                pass
    
    # Strategy 5: Try parsing line by line to find JSON
    lines = cleaned.split('\n')
    json_lines = []
    in_json = False
    
    for line in lines:
        if line.strip().startswith('{') or in_json:
            in_json = True
            json_lines.append(line)
            if line.strip().endswith('}'):
                try:
                    return json.loads('\n'.join(json_lines))
                except json.JSONDecodeError:
                    continue
    
    return {"error": "Failed to extract valid JSON from response", "raw_response": cleaned[:500] + "..." if len(cleaned) > 500 else cleaned}

def generate_schema(description: str, schema_type: str) -> Dict:
    try:
        chain = schema_prompt | llm
        response = chain.invoke({
            "description": description,
            "schema_type": schema_type
        })
        result = extract_json_from_response(response)
        
        if "error" in result:
            result["raw_response"] = response[:1000]
        return result
    except Exception as e:
        return {"error": f"Schema generation failed: {str(e)}", "raw_response": str(e)}

def create_er_diagram(schema: Dict) -> graphviz.Digraph:
    dot = graphviz.Digraph(comment='ER Diagram')
    dot.attr(rankdir='LR')

    if schema.get('error'):
        return dot

    items = schema.get('tables', []) if schema.get('schema_type') == 'relational' else schema.get('collections', [])
    for item in items:
        fields = '\n'.join([f"{f['name']}: {f['type']}" for f in item.get('fields', [])])
        dot.node(item['name'], f"{item['name']}\n{fields}")

    for item in items:
        for rel in item.get('relationships', []):
            dot.edge(
                item['name'],
                rel['related_to'],
                label=f"{rel['type']}\n{rel['field']}"
            )

    return dot

# Streamlit UI
st.title("AI-Powered Database Schema Designer")

description = st.text_area(
    "Describe your application",
    placeholder="Example: An ecommerce platform with customers, orders, products, and reviews. Customers can place multiple orders, and each order can contain multiple products."
)
schema_type = st.selectbox("Select Schema Type", ["Relational", "NoSQL"])

if st.button("Generate Schema"):
    if description:
        with st.spinner("Generating schema..."):
            schema = generate_schema(description, schema_type.lower())
            if schema.get('error'):
                st.error(f"{schema['error']}\n\nRaw response preview:\n{schema.get('raw_response', 'None')}")
            else:
                st.subheader("Generated Schema")
                st.json(schema)

                st.subheader("Schema Explanation")
                st.write(schema.get('explanation', 'No explanation provided'))

                st.subheader("ER Diagram")
                try:
                    dot = create_er_diagram(schema)
                    st.graphviz_chart(dot)
                except Exception as e:
                    st.error(f"Failed to generate ER diagram: {str(e)}")

                st.subheader("Feedback")
                satisfaction = st.slider("How satisfied are you with the generated schema? (0-100%)", 0, 100, 50)
                if st.button("Submit Feedback"):
                    st.success(f"Thank you for your {satisfaction}% satisfaction rating!")
    else:
        st.error("Please provide an application description")

# Sample test cases
st.subheader("Try Sample Descriptions")
sample_descriptions = [
    "A blog platform with users who can write multiple posts. Each post can have multiple comments from different users.",
    "An e-commerce store with products, categories, and customers. Customers can place orders containing multiple products.",
    "A task management system where users can create projects and add tasks. Tasks can be assigned to multiple users."
]

for sample in sample_descriptions:
    if st.button(f"Test: {sample[:50]}..."):
        st.session_state['description'] = sample
        st.experimental_rerun()