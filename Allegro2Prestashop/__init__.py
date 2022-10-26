import Allegro2Prestashop.core
import logging
import configparser


def main():
    config = configparser.ConfigParser()
    config.read('conf/config.ini')
    log_level = int(config['log']['log_level'])

    logging.basicConfig(filename='logs/app.log', filemode='w', level=log_level,
                        format="%(asctime)s  — %(levelname)s — %(message)s")
    fetcher = core.FetchAllegro()
    wrapper = core.PSApiWrapper()

    prices, skipped = fetcher.get_prices()
    wrapper.update_all(prices, skipped)


if __name__ == "__main__":
    logging.error('Wrong file! Run run.py instead.')
