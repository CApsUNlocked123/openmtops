install:
	pip install -r requirements.txt

run:
	python app.py

test:
	TESTING=1 python app.py
