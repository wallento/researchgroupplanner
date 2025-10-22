# Build

First we have to setup our python envitonment:

```shell
pip install -r requirements.txt
```

An alternative would be using `uv` and `direnv` for a virtual environment:
``` shell
uv venv
echo "source .venv/bin/activate" > .envrc
direnv allow
uv pip install -r requirements.txt
```

Then we setup Django

``` shell
python manage.py makemigrations projects staffing
python manage.py migrate
```

# Create admin

```shell
python manage.py createsuperuser
```

# Run

```shell
python manage.py runserver
```

# Create data in DB

Go to http://localhost:8000/admin