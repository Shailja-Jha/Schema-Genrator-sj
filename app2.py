import streamlit as st
import os
import json
import re
import subprocess
import time
import requests
from typing import Dict
from langchain_core.prompts import PromptTemplate
from langchain_community.llms import Ollama
import pymysql
import pymongo
from pymongo import MongoClient
import mysql.connector
from mysql.connector import Error

# ==============================================
# OLLAMA SERVER MANAGEMENT
# ==============================================

def is_ollama_installed():
    try:
        subprocess.run(["ollama", "--version"],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=True)
        return True
    except:
        return False

def start_ollama_server():
    try:
        if os.name == 'nt':
            subprocess.Popen(["ollama", "serve"],
                             creationflags=subprocess.CREATE_NEW_CONSOLE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        else:
            subprocess.Popen(["ollama", "serve"],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             start_new_session=True)
        return True
    except Exception as e:
        st.sidebar.error(f"Failed to start Ollama: {str(e)}")
        return False

def check_server_ready(timeout=15):
    for i in range(timeout):
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=2)
            if response.status_code == 200:
                st.sidebar.success(f"✅ Ollama ready after {i+1} seconds")
                return True
        except requests.exceptions.RequestException:
            time.sleep(1)
    return False

def ensure_ollama_running():
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            st.sidebar.success("✅ Ollama already running!")
            return True
    except requests.exceptions.RequestException:
        pass

    st.sidebar.warning("🚀 Starting Ollama server...")
    if not start_ollama_server():
        return False

    return check_server_ready()

def show_installation_instructions():
    st.error("""
    ## Ollama Setup Required
    
    1. **Install Ollama**:
       - Windows: Download from [ollama.com](https://ollama.com)
       - Mac/Linux: Run `curl -fsSL https://ollama.com/install.sh | sh`
    
    2. **Start Ollama** in a separate terminal:
       ```bash
       ollama serve
       ```
       (Keep this terminal open)
    
    3. **Download the model**:
       ```bash
       ollama pull mistral
       ```
    
    4. **Refresh this page** after setup
    """)
    st.stop()

# ==============================================
# DATABASE CONNECTION FUNCTIONS
# ==============================================

def get_mysql_connection():
    try:
        connection = mysql.connector.connect(
            host=st.session_state.mysql_host,
            user=st.session_state.mysql_user,
            password=st.session_state.mysql_password,
            database=st.session_state.mysql_database
        )
        return connection
    except Error as e:
        st.error(f"MySQL Connection Error: {str(e)}")
        return None

def get_mongo_connection():
    try:
        if st.session_state.mongo_username and st.session_state.mongo_password:
            connection_string = f"mongodb://{st.session_state.mongo_username}:{st.session_state.mongo_password}@{st.session_state.mongo_host}/{st.session_state.mongo_database}"
        else:
            connection_string = f"{st.session_state.mongo_host}"
        
        client = MongoClient(connection_string)
        # Test the connection
        client.admin.command('ping')
        return client
    except Exception as e:
        st.error(f"MongoDB Connection Error: {str(e)}")
        return None

def execute_mysql_queries(queries):
    connection = get_mysql_connection()
    if not connection:
        return False
    
    try:
        cursor = connection.cursor()
        for query in queries.split(';'):
            if query.strip():
                cursor.execute(query)
        connection.commit()
        return True
    except Error as e:
        st.error(f"MySQL Query Error: {str(e)}")
        return False
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def create_mongo_collections(schema):
    client = get_mongo_connection()
    if not client:
        return False
    
    try:
        db = client[st.session_state.mongo_database]
        
        for collection in schema.get('collections', []):
            # Create collection (MongoDB creates collections implicitly on first insert)
            db[collection['name']].insert_one({})  # Insert and then delete a dummy document
            db[collection['name']].delete_many({})  # Clean up
            
            # Create indexes based on constraints
            indexes = []
            for field in collection.get('fields', []):
                if 'unique' in field.get('constraints', []):
                    indexes.append((field['name'], pymongo.ASCENDING))
            
            if indexes:
                db[collection['name']].create_index(indexes, unique=True)
        
        return True
    except Exception as e:
        st.error(f"MongoDB Operation Error: {str(e)}")
        return False
    finally:
        client.close()

# ==============================================
# SCHEMA GENERATION FUNCTIONS
# ==============================================

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
    "explanation": "Brief design explanation",
    "sql_code": "Include valid SQL CREATE TABLE statements for all tables, including PRIMARY and FOREIGN KEY constraints if relational. If nosql, leave empty string.",
    "prisma_code": "Include valid Prisma schema code if nosql. If relational, leave empty string."
}}

IMPORTANT RULES:
1. Output ONLY the raw JSON with NO additional text
2. Ensure all quotes are straight double quotes (")
3. No trailing commas in arrays/objects
4. No comments or explanations outside the JSON
5. The "sql_code" must be valid SQL CREATE statements for relational schemas
6. The "prisma_code" must be valid Prisma schema for NoSQL databases
7. Both code fields must come at the end (after explanation)
8. All brackets and braces must be properly closed

BEGIN OUTPUT:
"""
)

def extract_json_from_response(response: str) -> Dict:
    cleaned = response.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    code_blocks = re.findall(r'```(?:json)?\n(.*?)\n```', cleaned, re.DOTALL)
    for block in code_blocks:
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            continue

    start_idx = cleaned.find('{')
    end_idx = cleaned.rfind('}')
    if start_idx != -1 and end_idx != -1:
        potential_json = cleaned[start_idx:end_idx + 1]
        try:
            return json.loads(potential_json)
        except json.JSONDecodeError:
            try:
                fixed = re.sub(r',\s*([}\]])', r'\1', potential_json)
                return json.loads(fixed)
            except json.JSONDecodeError:
                pass

    return {"error": "Failed to extract valid JSON from response", "raw_response": cleaned[:500] + "..."}

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

def create_mermaid_diagram(schema: Dict) -> str:
    """Generate Mermaid ER diagram code with better error handling"""
    if not schema or schema.get('error'):
        return """erDiagram
    error((Error: Invalid Schema))"""
    
    try:
        diagram = ["erDiagram"]
        items = schema.get('tables', []) if schema.get('schema_type') == 'relational' else schema.get('collections', [])
        
        # 1. Create entities with their attributes
        for item in items:
            if not isinstance(item, dict):
                continue
                
            entity_lines = [f"{item.get('name', 'unnamed')} {{"]
            for field in item.get('fields', []):
                if not isinstance(field, dict):
                    continue
                field_type = str(field.get('type', 'string')).upper()
                field_name = field.get('name', 'unknown')
                entity_lines.append(f"  {field_type} {field_name}")
            entity_lines.append("}")
            diagram.append("\n".join(entity_lines))
        
        # 2. Add relationships
        for item in items:
            if not isinstance(item, dict):
                continue
                
            for rel in item.get('relationships', []):
                if not isinstance(rel, dict):
                    continue
                    
                src = item.get('name', 'unknown')
                dest = rel.get('related_to', 'unknown')
                rel_type = str(rel.get('type', '1:N')).upper().replace("-", ":")
                rel_field = rel.get('field', '')
                
                # Standardize relationship types
                if rel_type in ["1:1", "ONE_TO_ONE"]:
                    connector = "||--||"
                elif rel_type in ["1:N", "ONE_TO_MANY"]:
                    connector = "||--o{"
                elif rel_type in ["M:N", "MANY_TO_MANY"]:
                    connector = "}o--o{"
                else:
                    connector = "--"
                
                diagram.append(f'{src} {connector} {dest} : "{rel_field}"')
        
        return "\n".join(diagram)
    
    except Exception as e:
        return f"""erDiagram
    error((Diagram Generation Failed))
    note{{
        {str(e)[:100]}
    }}"""

def render_mermaid_chart(mermaid_code: str):
    """More reliable rendering with multiple fallbacks"""
    try:
        # Try native Streamlit mermaid first
        st.markdown(f"```mermaid\n{mermaid_code}\n```")
    except:
        try:
            # Fallback to HTML if native fails
            html = f"""
            <div class="mermaid">
                {mermaid_code}
            </div>
            <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
            <script>mermaid.initialize({{startOnLoad:true}});</script>
            """
            st.components.v1.html(html, height=500)
        except:
            # Final fallback to plain code display
            st.error("Mermaid rendering failed - here's the raw code:")
            st.code(mermaid_code, language="mermaid")

# ==============================================
# STREAMLIT UI
# ==============================================

def show_database_connection_form():
    with st.expander("Database Connection Settings", expanded=False):
        db_type = st.selectbox("Database Type", ["MySQL", "MongoDB"])
        
        if db_type == "MySQL":
            st.session_state.mysql_host = st.text_input("MySQL Host", "localhost")
            st.session_state.mysql_user = st.text_input("MySQL Username", "root")
            st.session_state.mysql_password = st.text_input("MySQL Password", type="password")
            st.session_state.mysql_database = st.text_input("MySQL Database Name", "my_database")
            
            if st.button("Test MySQL Connection"):
                conn = get_mysql_connection()
                if conn:
                    st.success("✅ MySQL connection successful!")
                    conn.close()
        else:
            st.session_state.mongo_host = st.text_input("MongoDB URL", "")
            st.session_state.mongo_username = st.text_input("MongoDB Username (optional)")
            st.session_state.mongo_password = st.text_input("MongoDB Password (optional)", type="password")
            
            if st.button("Test MongoDB Connection"):
                client = get_mongo_connection()
                if client:
                    st.success("✅ MongoDB connection successful!")
                    client.close()

def main():
    st.title("AI-Powered Database Schema Designer")

    if not is_ollama_installed():
        show_installation_instructions()
    if not ensure_ollama_running():
        show_installation_instructions()

    global llm
    llm = Ollama(
        model="mistral",
        temperature=0.3,
        num_ctx=2000,
        timeout=120
    )

    # Database connection form
    show_database_connection_form()

    description = st.text_area(
        "Describe your application",
        placeholder="Example: An ecommerce platform with customers, orders, products, and reviews..."
    )

    schema_type = st.selectbox("Select Schema Type", ["Relational", "NoSQL"])

    if st.button("Generate Schema"):
        if description:
            with st.spinner("Generating schema..."):
                schema = generate_schema(description, schema_type.lower())

                if schema.get('error'):
                    st.error(f"{schema['error']}\n\nRaw response preview:\n{schema.get('raw_response', 'None')}")
                else:
                    st.session_state.generated_schema = schema
                    st.subheader("Generated Schema")
                    st.json(schema)

                    st.subheader("Schema Explanation")
                    st.write(schema.get('explanation', 'No explanation provided'))

                    st.subheader("Mermaid ER Diagram")
                    try:
                        mermaid_code = create_mermaid_diagram(schema)
                        render_mermaid_chart(mermaid_code)
                        with st.expander("Show Mermaid Code"):
                            st.code(mermaid_code, language="mermaid")
                    except Exception as e:
                        st.error(f"Failed to generate Mermaid diagram: {str(e)}")

                    # SQL Code Output
                    if schema.get("sql_code"):
                        st.subheader("SQL Schema Code")
                        st.code(schema["sql_code"], language="sql")

                    # Prisma Code Output
                    if schema.get("prisma_code"):
                        st.subheader("Prisma Schema Code")
                        st.code(schema["prisma_code"], language="prisma")

                    st.subheader("Feedback")
                    satisfaction = st.slider("Rate your satisfaction (0-100%)", 0, 100, 50)
                    if st.button("Submit Feedback"):
                        st.success(f"Thank you for your feedback! ({satisfaction}%)")
        else:
            st.error("Please provide an application description")

    # Deploy to database section
    if 'generated_schema' in st.session_state:
        schema = st.session_state.generated_schema
        st.divider()
        st.subheader("Deploy to Database")
        
        if schema.get('schema_type') == 'relational' and schema.get('sql_code'):
            if st.button("Create Tables in MySQL"):
                if execute_mysql_queries(schema['sql_code']):
                    st.success("✅ Tables created successfully in MySQL!")
                else:
                    st.error("Failed to create tables in MySQL")
        
        elif schema.get('schema_type') == 'nosql' and schema.get('collections'):
            if st.button("Create Collections in MongoDB"):
                if create_mongo_collections(schema):
                    st.success("✅ Collections created successfully in MongoDB!")
                else:
                    st.error("Failed to create collections in MongoDB")

    # st.subheader("Try Sample Descriptions")
    # samples = [
    #     "A blog platform with users, posts, and comments",
    #     "E-commerce store with products, categories, and orders",
    #     "Task management system with projects and assignments"
    # ]

    # for sample in samples:
    #     if st.button(sample):
    #         st.session_state['description'] = sample
    #         st.experimental_rerun()

if __name__ == "__main__":
    main()