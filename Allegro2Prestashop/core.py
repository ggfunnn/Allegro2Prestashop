import requests
from requests.exceptions import HTTPError, ConnectionError
import configparser
import base64
import smtplib
import time
import json
from os import path
import logging
from xml.etree import ElementTree
from xml.etree.ElementTree import ParseError
import concurrent.futures


class FetchAllegro(object):
    """Fetches data from Allegro API"""

    def __init__(self):
        config = configparser.ConfigParser()
        config.read('conf/config.ini')

        self.client_id = config['allegro']['client_id']
        self.client_secret = config['allegro']['client_secret']

        self.receiver = config['mail_auth']['receiver']
        self.subject = config['mail_auth']['subject']
        self.content = config['mail_auth']['content'].replace("\\n", "\n")
        self.user = config['mail_auth']['user']
        self.passwd = config['mail_auth']['passwd']
        self.server = config['mail_auth']['server']
        self.port = int(config['mail_auth']['port'])

        self.log_level = int(config['log']['log_level'])

        self.api_url = 'https://api.allegro.pl/'
        self.auth_url = 'https://allegro.pl/auth/oauth/'

        self.token = self._authorize()

        self.products = []
        self.skipped = 0
        self.products_count = 1
        self.offers_quantity = self._get_offers_quantity()

        logging.debug('Successfully initialized config.')
        logging.debug('Class Fetch Allegro initialized!')

    def _encode(self):
        str_to_encode = self.client_id + ':' + self.client_secret
        b64_secrets = base64.b64encode(str_to_encode.encode('ascii')).decode('ascii')
        logging.debug('Successfully encoded secrets.')

        return b64_secrets

    def _send_mail(self, content):
        mail_tpl = f'From: {self.user}' + f'\nTo: {self.receiver}' + f'\nSubject: {self.subject}' + f'\n\n{content}'

        try:
            server = smtplib.SMTP_SSL(self.server, self.port)

            if self.log_level <= 10:
                server.set_debuglevel(True)

            server.ehlo()
            server.login(self.user, self.passwd)
            server.sendmail(self.user, self.receiver.split(', '), mail_tpl.encode('utf-8'))
            server.close()

        except Exception as e:
            logging.error('Something went wrong with mail...' + str(e))

        else:
            logging.info('Successfully sent token refresh email!')

    @staticmethod
    def _store_tokens(data):
        try:
            with open('conf/token.json', 'w+') as outfile:
                outfile.truncate(0)
                json.dump(data, outfile)

        except Exception as e:
            logging.error('Something went wrong with storing token... ' + str(e))

        else:
            logging.debug('Successfully stored tokens!')

    @staticmethod
    def _get_tokens():
        try:
            with open('conf/token.json', 'r') as infile:
                token = json.load(infile)

        except Exception as e:
            logging.error('Something went wrong while getting the token... ' + str(e))

        else:
            logging.debug('Successfully retrieved tokens from file!')
            return token

    def _new_token(self, b64_secrets):
        try:
            auth_request = requests.post(self.auth_url + 'device?client_id=' + self.client_id,
                                         headers={'Authorization': 'Basic ' + b64_secrets,
                                                  'Content-Type': 'application/x-www-form-urlencoded'})

            self.auth_response = auth_request.json()

            auth_request.raise_for_status()
            self._send_mail(self.content + self.auth_response['verification_uri_complete'])

            while True:
                check_request = requests.post(
                    self.auth_url +
                    'token?grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code&device_code=' +
                    self.auth_response["device_code"], headers={'Authorization': 'Basic ' + b64_secrets})
                check_response = check_request.json()

                if 'access_token' in check_response:
                    self._store_tokens(check_response)
                    token = check_response['access_token']
                    logging.debug('Token access granted!')
                    break

                elif check_response["error"] == 'authorization_pending':
                    logging.info('Authorization pending...')

                else:
                    raise RuntimeError(f'{check_response["error"]}')

                time.sleep(self.auth_response["interval"])

        except HTTPError as http_error:
            logging.error(f'HTTP error occurred while getting a new token: {http_error}')

        except Exception as error:
            logging.error(f'Other error occurred while getting a new token: {error}')

        else:
            logging.debug('The new token is now authorized!')
            return token

    def _authorize(self):
        try:
            b64_secrets = self._encode()
            if path.isfile('conf/token.json') and path.getsize('conf/token.json') != 0:
                old_tokens = self._get_tokens()

                refresh_response = requests.get(self.auth_url + 'token?grant_type=refresh_token&refresh_token=' +
                                                old_tokens["refresh_token"], headers={'Authorization': 'Basic ' +
                                                                                                       b64_secrets
                                                                                      }).json()

                if 'access_token' in refresh_response:
                    self._store_tokens(refresh_response)
                    token = refresh_response["access_token"]
                    logging.debug('Authorized by refresh token!')

                elif 'error' in refresh_response:
                    token = self._new_token(b64_secrets)

                else:
                    raise RuntimeError('Undefined error when refreshing the token. '
                                       'Please contact with the administrator.')
            else:
                token = self._new_token(b64_secrets)

        except HTTPError as http_error:
            logging.error(f'HTTP error occurred while authorizing: {http_error}')

        except Exception as error:
            logging.error(f'Other error occurred while authorizing: {error}')

        else:
            logging.info('Successfully authorized - Allegro')
            return token

    def _get_offers_quantity(self):
        try:
            offers_quantity_request = requests.get(self.api_url + 'sale/offers?limit=1&offset=0',
                                                   headers={'Authorization': 'Bearer ' + self.token,
                                                            'Accept': 'application/vnd.allegro.public.v1+json'})
            offers_quantity = int(offers_quantity_request.json()["totalCount"])

        except HTTPError as http_error:
            logging.error(f'HTTP error occurred while getting offers quantity: {http_error}')
        except Exception as error:
            logging.error(f'Other error occurred while getting offers quantity: {error}')
        else:
            return offers_quantity

    def _get_price(self, s, offer, first=True):
        try:
            offer_request = s.get(self.api_url + 'sale/offers/' + offer["id"])
            offer_response = offer_request.json()

            if offer_response["external"] is not None:
                if offer_response["external"]["id"] == '*':
                    self.skipped += 1
                    raise RuntimeError(str(self.products_count) + '/' + str(self.offers_quantity) +
                                       ': Product on blacklist - * detected!')

            product = []
            for parameter in offer_response["parameters"]:
                if parameter["id"] == '225693':
                    product.append(str(parameter["values"][0]))

            if not product:
                product = [None, offer_response["sellingMode"]["price"]["amount"], offer["id"]]
                self.products.append(product)

                raise RuntimeError(str(self.products_count) + '/' + str(self.offers_quantity) + ': EAN not found!')

            product.extend((offer_response["sellingMode"]["price"]["amount"], offer["id"]))
            self.products.append(product)

            logging.info(str(self.products_count) + '/' + str(self.offers_quantity))

        except ConnectionError as con_error:
            logging.error(f'Connection error occurred while getting the price: {con_error}')
            if "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))" == \
                    str(con_error):
                if first:
                    logging.warning('Trying again due to the dropped connection...')
                    self._get_price(s, offer, first=False)

        except HTTPError as http_error:
            logging.error(f'HTTP error occurred while getting the price: {http_error}')

        except RuntimeError as run_error:
            logging.error(f'An error occurred while getting the price: {run_error}')

        except Exception as error:
            logging.error(f'Other error occurred while getting the price: {error}')

        else:
            if not first:
                logging.info('Successfully got price after retry!')

        finally:
            self.products_count += 1

    def get_prices(self):
        with requests.Session() as s:
            s.headers.update({'Authorization': 'Bearer ' + self.token,
                              'Accept': 'application/vnd.allegro.public.v1+json'})

            for i in range(0, self.offers_quantity, 1000):
                try:
                    offers_request = s.get(self.api_url + 'sale/offers?limit=1000&offset=' + str(i))

                    offers = offers_request.json()
                    offers_request.raise_for_status()

                except HTTPError as http_error:
                    logging.exception(f'HTTP error occurred while getting prices: {http_error}')

                except Exception as error:
                    logging.exception(f'Other error occurred while getting prices: {error}')

                else:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                        futures = []
                        for offer in offers["offers"]:
                            futures.append(executor.submit(self._get_price, s=s, offer=offer))

                logging.info('Successfully fetched offers ' + str(i) + '-' + str(i + 1000) + '!')

        return self.products, self.skipped


class PSApiWrapper(object):
    """Prestashop API wrapper class"""

    def __init__(self):
        config = configparser.ConfigParser()
        config.read('conf/config.ini')

        self.api_url = config['api']['url']
        self.api_key = config['api']['key']
        self.token = self._encode()

        self.receiver = config['mail']['receiver']
        self.subject = config['mail']['subject']
        self.content_lang = config['mail']['content_lang']
        self.user = config['mail']['user']
        self.passwd = config['mail']['passwd']
        self.server = config['mail']['server']
        self.port = int(config['mail']['port'])

        self.log_level = config['log']['log_level']

        logging.debug('Config initialized.')
        logging.debug('Class PSAApiWrapper initialized!')

    def _encode(self):
        try:
            token = base64.b64encode(self.api_key.encode('ascii')).decode('ascii')

        except Exception as error:
            logging.error(f'Error while encoding the token: {error}')

        else:
            logging.debug('Token encoded.')
            return token

    def _update(self, product_id, price, s, first=True):
        try:
            get_request = s.get(self.api_url + 'products/' + product_id)

            get_response = get_request.content

            tree = ElementTree.fromstring(get_response)
            net_price = str(round((float(price) / 1.23), 2))
            tree.find("./product/price").text = net_price
            product = tree.find("./product")
            product.remove(tree.find("./product/manufacturer_name"))
            product.remove(tree.find("./product/quantity"))

            update_xml = ElementTree.tostring(tree, encoding='utf8', method='xml')

            get_request.raise_for_status()
            logging.debug('Sent get xml form request')

            update_request = s.put(self.api_url + 'products', headers={'Io-Format': 'JSON'}, data=update_xml)

            update_response = update_request.json()
            update_request.raise_for_status()
            logging.debug('Sent update price request')

        except HTTPError as http_error:
            logging.exception('HTTP error occurred while updating product ' + product_id + f': {http_error}')
            if '500 Server Error' in str(http_error):
                if first:
                    logging.warning('Trying again due to Server Error...')
                    self._update(product_id, price, s, first=False)

        except ParseError as parse_error:
            logging.exception(f'Parsing error occurred while updating product ' + product_id + f': {parse_error}')
            if 'no element found: line 1, column 0' in str(parse_error):
                logging.warning('Probably bugged product! Please re-add it.')

        except Exception as error:
            logging.exception(f'Other error occurred while updating product ' + product_id + f': {error}')
            if 'Operation timed out' in str(error):
                if first:
                    logging.warning('Trying again due to operation timeout...')
                    self._update(product_id, price, s, first=False)

        else:
            if update_response["product"]["id"] == product_id:
                if update_response["product"]["price"] == net_price:
                    logging.debug('Successfully updated product ' + product_id)
                    if not first:
                        logging.info("Successfully updated product " + product_id + " after retry!")

            else:
                logging.error('Undefined error occurred while updating product ' + product_id)

    def _get_ids(self):
        all_ids = []

        try:
            check_request = requests.get(self.api_url + 'products?display=[id,ean13]',
                                         headers={'Authorization': 'Basic ' + self.token,
                                                  'Output-Format': 'JSON'})

            check_response = check_request.json()
            check_request.raise_for_status()
            logging.debug('Sent get all ids request')

            for product in check_response["products"]:
                if product["ean13"] == '':
                    s = [None, str(product["id"])]
                else:
                    s = [product["ean13"], str(product["id"]), False]
                all_ids.append(s)

        except HTTPError as http_error:
            logging.error(f'HTTP error occurred while getting ids: {http_error}')

        except Exception as error:
            logging.error(f'Other error occurred while getting ids: {error}')

        else:
            return all_ids

    def _merge_all(self, ids, prices):
        try:
            merged = []
            for stock in prices[:]:
                if stock[0] is None:
                    prices.remove(stock)
                    stock[0] = 'EAN - Allegro'
                    merged.append([stock[0], stock[2]])
                    continue

                for product in ids[:]:
                    if product[0] is None:
                        ids.remove(product)
                        product[0] = 'EAN - PS'
                        merged.append(product)
                        break

                    elif stock[0] == product[0]:
                        merged.append([product[0], product[1], str(stock[1])])
                        if not product[2]:
                            ids.remove(product)
                            product[2] = True
                            ids.append(product)
                        prices.remove(stock)
                        logging.debug('Successfully merged product: ' + product[0])
                        break

            if self.content_lang == 'pl':
                for product in ids:
                    if not product[2]:
                        product = ['Niedopasowano PS', product[1]]
                        merged.append(product)
                        logging.debug('Mismatched product: ' + product[1])

                for stock in prices:
                    stock = ['Niedopasowano Allegro', stock[2]]
                    merged.append(stock)
                    logging.debug('Mismatched product: ' + stock[1])

            else:
                if self.content_lang != 'en':
                    logging.error(
                        'Error: Content language "' + self.content_lang + '"is not supported. Using en instead.')

                for product in ids:
                    if not product[2]:
                        product = ['Mismatched PS', product[1]]
                        merged.append(product)
                        logging.debug('Mismatched product: ' + product[1])

                for stock in prices:
                    stock = ['Mismatched Allegro', stock[2]]
                    merged.append(stock)
                    logging.debug('Mismatched product: ' + stock[1])

        except Exception as e:
            logging.error('Something went wrong with merging lists: ' + str(e))

        else:
            logging.info('Successfully merged lists!')
            return merged

    def _send_report(self, updated, not_updated, skipped):
        str_not_updated = '\n'.join(map(str, list(map(' '.join, not_updated))))

        if self.content_lang == 'pl':
            content = 'Zaktualizowane produkty: ' + str(len(updated)) + '\n\nPominięte produkty: \n' \
                      + str(skipped) + '\n\nNiezaktualizowane produkty:\n' + str_not_updated + '\nRazem: ' \
                      + str(len(not_updated))
            logging.debug('Using pl mail template')

        elif self.content_lang == 'en':
            content = 'Updated products: ' + str(len(updated)) + '\n\nSkipped products: \n' \
                      + str(skipped) + '\n\nNot updated products:\n' + str_not_updated + '\nTotal: ' \
                      + str(len(not_updated))
            logging.debug('Using en mail template')

        else:
            logging.error('Error: Content language "' + self.content_lang + '"is not supported. Using en instead.')
            content = 'Updated products: ' + str(len(updated)) + '\n\nSkipped products: \n' \
                      + str(skipped) + '\n\nNot updated products:\n' + str_not_updated + '\nTotal: ' \
                      + str(len(not_updated))

        mail_tpl = f'From: {self.user}' + f'\nTo: {self.receiver}' + f'\nSubject: {self.subject}' + f'\n\n{content}'

        try:
            server = smtplib.SMTP_SSL(self.server, self.port)
            logging.debug('SMTP server connection established')

            if self.log_level <= '10':
                server.set_debuglevel(True)

            server.ehlo()
            server.login(self.user, self.passwd)
            logging.debug('Logged into SMTP server')
            server.sendmail(self.user, self.receiver.split(', '), mail_tpl.encode('utf-8'))
            logging.debug('Sent mail')
            server.close()
            logging.debug('SMTP server connection closed')

        except Exception as e:
            logging.error('Something went wrong with mail: ' + str(e))

        else:
            logging.info('Successfully sent report!')

    def update_all(self, prices, skipped):
        updated = []
        not_updated = []

        ids = self._get_ids()
        products_params = self._merge_all(ids, prices)

        with requests.Session() as s:
            s.headers.update({'Authorization': 'Basic ' + self.token})
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = []
                i = 0
                NOT_TO_UPDATE = ['EAN - Allegro', 'EAN - PS', 'Mismatched PS', 'Niedopasowano PS', 'Mismatched Allegro',
                                 'Niedopasowano Allegro']
                for product in products_params:
                    if product[0] in NOT_TO_UPDATE:
                        not_updated.append(product)

                    else:
                        futures.append(executor.submit(self._update, product_id=product[1], price=product[2], s=s))
                        updated.append(product[1])

                for _ in concurrent.futures.as_completed(futures):
                    i += 1
                    logging.info(str(i) + '/' + str(len(futures)))

        self._send_report(updated, not_updated, skipped)
