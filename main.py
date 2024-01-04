from bs4 import BeautifulSoup
from telebot.async_telebot import AsyncTeleBot
import asyncio
import aiohttp
import aiomysql
from transformers import pipeline
from transformers import MBartForConditionalGeneration, MBart50TokenizerFast

db_config = {
    'host': "localhost",
    'port': 8889,
    'user': "root",
    'password': "root",
    'db': "telepost"
}

bot = AsyncTeleBot('6703960437:AAGnQ2bCFm8HvJe_p0e9s')
API_URL = "https://api-inference.huggingface.co/models/facebook/bart-large-cnn"
headers = {"Authorization": "Bearer J"}
summarizer = pipeline("summarization", model="facebook/bart-large-cnn")
model = MBartForConditionalGeneration.from_pretrained("facebook/mbart-large-50-many-to-many-mmt")
tokenizer = MBart50TokenizerFast.from_pretrained("facebook/mbart-large-50-many-to-many-mmt")


def text_translation(text):
    tokenizer.src_lang = "en_XX"
    encoded_ar = tokenizer(text, return_tensors="pt")
    generated_tokens = model.generate(
        **encoded_ar,
        forced_bos_token_id=tokenizer.lang_code_to_id["ru_RU"]
    )
    return tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)


class Parser:
    def __init__(self):
        self.links = []
        self.headers = []

    async def parse_initial(self, url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                page = await response.text()
                soup = BeautifulSoup(page, 'html.parser')
                section = soup.find_all('section', class_='category_contents_details')[0]
                articles = section.find_all('article')
                for link in articles:
                    self.links.append(link.find('a').get('href'))
                    h1, paragraph, img_url = await self.parse_articles(link.find('a').get('href'))
                    self.headers.append(
                        {'h1': h1.text.strip(), 'paragraph': paragraph, 'link': link.find('a').get('href'),
                         'img_url': img_url})
                return self.headers

    async def parse_articles(self, url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                page = await response.text()
                soup = BeautifulSoup(page, 'html.parser')
                article_div = soup.find_all('div', class_='category_contents_details')[0]
                h1 = article_div.find('h1')
                p = article_div.find_all('p')
                paragraphs = ''.join([txt.text.strip() for txt in p])

                # Попытка найти изображение в теге <h2>
                img_url = None
                h2_tag = article_div.find('h2')
                if h2_tag:
                    img_tag = h2_tag.find('img')
                    if img_tag:
                        img_url = img_tag.get('data-lazy-src')  # Предполагаем, что тег <img> использует атрибут 'src'

                # Если в <h2> изображения нет, ищем в <figure>
                if not img_url:
                    figures = article_div.find_all('figure')
                    if figures:
                        img_tag = figures[0].find('img')
                        if img_tag:
                            img_url = img_tag.get('data-lazy-src')

                return h1, paragraphs, img_url

    async def tg_send_message(self, message_tuple):
        max_length_caption = 1024  # Максимальная длина подписи для фотографии в Telegram
        max_length_message = 4096  # Максимальная длина текстового сообщения в Telegram
        message, image_url = "\n\n\n".join(message_tuple[:3]), message_tuple[3]

        async with aiomysql.connect(**db_config) as conn:
            async with conn.cursor() as cursor:
                query_chat_id = "SELECT `chat_id` FROM teleinfo"
                await cursor.execute(query_chat_id)
                chat_ids = await cursor.fetchall()
                for chat_id_tuple in chat_ids:
                    chat_id = chat_id_tuple[0]

                    # Отправка фото с подписью
                    if image_url:
                        caption = message if len(message) <= max_length_caption else message[:max_length_caption]
                        try:
                            await bot.send_photo(chat_id, image_url, caption=caption)
                            await asyncio.sleep(1)
                            print("Message sent to chat_id", chat_id)
                        except Exception as e:
                            print(f"Error sending photo to chat_id {chat_id}: {e}")

                    # Если сообщение длиннее максимальной длины подписи, отправляем остаток текста отдельно
                    if len(message) > max_length_caption:
                        additional_text = message[max_length_caption:] if image_url else message
                        parts = [additional_text[i:i + max_length_message] for i in
                                 range(0, len(additional_text), max_length_message)]
                        for part in parts:
                            try:
                                await bot.send_message(chat_id, part, disable_notification=False,
                                                       disable_web_page_preview=True)
                                print("Message sent to chat_id", chat_id)
                                await asyncio.sleep(1)
                            except Exception as e:
                                print(f"Error sending additional message to chat_id {chat_id}: {e}")

    async def add_to_db(self, data):
        async with aiomysql.connect(**db_config) as conn:
            async with conn.cursor() as cursor:
                for x in data:
                    query1 = "SELECT `link` FROM telebot WHERE `link` = %s"
                    await cursor.execute(query1, (x['link'],))
                    if not await cursor.fetchone():
                        word_count = len(x['paragraph'].split())  # Общее количество слов
                        message = summarizer(x['paragraph'], max_length=word_count - 10, min_length=150,
                                             do_sample=False)

                        query = "INSERT INTO telebot (header, h_translate, paragraph, p_translate, link, image_url) VALUES (%s, %s, %s, %s, %s, %s)"

                        values = (x['h1'], text_translation(x['h1'])[0], x['paragraph'],
                                  text_translation(message[0]['summary_text'])[0], x['link'], x['img_url'])

                        await cursor.execute(query, values)
                        await self.tg_send_message((x['h1'], message[0]['summary_text'], x['link'], x['img_url']))
                await conn.commit()


async def main():
    url = 'https://cryptonews.com/'
    parser = Parser()

    @bot.message_handler(commands=['help', 'start'])
    async def send_welcome(message):
        chat_id = message.chat.id
        print(f"chat_id to insert: {chat_id}")  # Для отладки
        async with aiomysql.connect(**db_config) as conn:
            async with conn.cursor() as cursor:
                query_t = "SELECT `chat_id` FROM teleinfo WHERE `chat_id` = %s"
                await cursor.execute(query_t, (chat_id,))
                if not await cursor.fetchone():
                    query = "INSERT INTO teleinfo (chat_id) VALUES (%s)"
                    await cursor.execute(query, (chat_id,))
                    await conn.commit()

    polling_task = asyncio.create_task(bot.polling())
    try:
        while True:
            print("Parsing website...")
            data = await parser.parse_initial(url)
            print(f"Found {len(data)} articles, updating database...")
            await parser.add_to_db(data)
            await asyncio.sleep(600)
    finally:
        polling_task.cancel()


if __name__ == '__main__':
    asyncio.run(main())
