# Build

```shell
pip install -r requirements.txt
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