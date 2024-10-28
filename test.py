'''
Test file to start messing around with AIC's public api
'''
import requests

url = 'https://api.artic.edu/api/v1/artworks'
params = {'id': 129884}


response = requests.get(url, params= params)
data = response.json()  # Convert the JSON response to a dictionary
print(data)