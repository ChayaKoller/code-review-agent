import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()
model = ChatGoogleGenerativeAI(model=os.environ["MODEL_NAME"])
print(model.invoke("Reply with one word: hello").content)