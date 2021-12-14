from config import (
    API_TOKEN,
    POEMS_COUNT,
    DATABASE_CHANNEL_USERNAME,
)
from poems import (
    poems,
    poems_info,
)
from search import (
    Searcher,
    index_of_matched_line_string,
    index_of_matched_line_words,
)

from itertools import starmap
from random import randrange
from re import match
from typing import (
    Callable,
    Union,
)
from uuid import uuid4

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
    User,
)
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    Filters,
    InlineQueryHandler,
    MessageHandler,
    Updater,
)


_BOT_USERNAME: str
_INLINE_HELP = 'inline-help'
_SEND_AUDIO = 'audio'
_FAVORITE_POEMS_QUERY = '#favorite_poems'
_SURROUNDED_WITH_DOUBLE_QUOTES = r'^"[\u0600-\u06FF\s]+"$'
_NO_MATCH_WAS_FOUND = 'جستجو نتیجه ای در بر نداشت❗️'

_searcher = Searcher()
_user_to_favorite_poems: dict[User, set[str]] = {}
_user_to_reply_with_line: dict[User, bool] = {}
_to_invoke: Callable[[], None]


############################
# CommandHandler callbacks #
############################

def start(update: Update, context: CallbackContext) -> None:
    args = context.args
    if args:
        if args[0] == _INLINE_HELP:
            update.message.reply_text(
                'بعد از نوشتن یوزرنیمِ بات در یک چت،\n'
                'با نوشتن چند کلمه از یک بیت حافظ، غزل یا بیتی را که\n'
                'یک بیتش شامل کلمات وارد شده، باشد دریافت خواهی کرد.\n'
                'در ضمن اگر می خواهی کل یک عبارت با هم (و نه تک تک کلماتش)\n'
                'در بیت جستجو شود، آن را درون "" بگذار.'
            )
        elif args[0].startswith(_SEND_AUDIO):
            poem_number = int(args[0].removeprefix(_SEND_AUDIO))
            context.bot.forward_message(
                chat_id=update.effective_chat.id,
                from_chat_id=DATABASE_CHANNEL_USERNAME,
                message_id=poem_number + 2   # channel message ID's start from 2
            )
    else:
        help_command(update, context)
        _user_to_favorite_poems[update.effective_user] = set()


def help_command(update: Update, _: CallbackContext) -> None:
    keyboard = [
        [
            InlineKeyboardButton('Github', 'https://github.com/ArminGh02/hafez-poems-telegram-bot'),
            InlineKeyboardButton('Developer', 'https://telegram.me/ArminGh02'),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        f'سلام {update.effective_user.first_name}!\n'
        'با نوشتن چند کلمه از یک بیت حافظ، غزل یا بیتی را که \n'
        'یک بیتش شامل کلمات وارد شده، باشد دریافت خواهی کرد.\n'
        'در ضمن اگر می خواهی کل یک عبارت با هم (و نه تک تک کلماتش)\n'
        'در بیت جستجو شود، آن را درون "" بگذار.\n'
        'همچنین با زدن دستور /fal یک فال می توانی بگیری.\n'
        f'تعداد کاربران: {max(len(_user_to_reply_with_line), len(_user_to_favorite_poems))}',
        reply_markup=reply_markup,
    )


def reply_line(update: Update, _: CallbackContext) -> None:
    _user_to_reply_with_line[update.effective_user] = True
    update.message.reply_text('از این پس در نتیجه جستجو، بیت را دریافت خواهی کرد.✅')


def reply_poem(update: Update, _: CallbackContext) -> None:
    _user_to_reply_with_line[update.effective_user] = False
    update.message.reply_text('از این پس در نتیجه جستجو، کل غزل را دریافت خواهی کرد.✅')


def random_poem_command(update: Update, _: CallbackContext) -> None:
    poem_number, poem = get_random_poem()
    meter = '🎼وزن: ' + poems_info[poem_number]['meter']
    update.message.reply_text(
        text=poem + meter,
        reply_markup=get_poem_keyboard(poem_number, poem, update.effective_user, False),
    )


def list_favorite_poems(update: Update, _: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton('لیست غزل های مورد علاقه ❤️', switch_inline_query_current_chat=_FAVORITE_POEMS_QUERY)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text('دکمه زیر را بزن.', reply_markup=reply_markup)


############################
# MessageHandler callbacks #
############################

def search_words(update: Update, _: CallbackContext) -> None:
    words = update.message.text.split()
    search_impl(update, words)


def search_string(update: Update, _: CallbackContext) -> None:
    string_to_search = update.message.text[1:-1]  # remove "" (double quotes) from start and end of string
    search_impl(update, string_to_search)


##################################
# CallbackQueryHandler callbacks #
##################################

def result_mode_chosen(update: Update, _: CallbackContext) -> None:
    query = update.callback_query
    user = update.effective_user

    if query.data == 'line':
        text = 'در نتیجه جستجو بیت دریافت می شود.'
        _user_to_reply_with_line[user] = True
    else:  # query.data == 'poem'
        text = 'در نتیجه جستجو کل غزل دریافت می شود.'
        _user_to_reply_with_line[user] = False

    query.edit_message_text(text)
    query.answer()
    _to_invoke()


def add_to_favorite_poems(update: Update, _: CallbackContext) -> None:
    user = update.effective_user
    query = update.callback_query

    poem_number = int(query.data.removeprefix('add'))
    poem = poems[poem_number]
    if user not in _user_to_favorite_poems:
        _user_to_favorite_poems[user] = {(poem_number, poem)}
    else:
        _user_to_favorite_poems[user].add((poem_number, poem))

    query.edit_message_reply_markup(get_poem_keyboard(poem_number, poem, user, update.effective_chat == None))
    query.answer('این غزل به لیست علاقه‌مندی‌های شما افزوده شد.')


def remove_from_favorite_poems(update: Update, _: CallbackContext) -> None:
    user = update.effective_user
    query = update.callback_query

    poem_number = int(query.data.removeprefix('remove'))
    poem = poems[poem_number]
    _user_to_favorite_poems[user].remove((poem_number, poem))

    query.edit_message_reply_markup(get_poem_keyboard(poem_number, poem, user, update.effective_chat == None))
    query.answer('این غزل از لیست علاقه‌مندی‌های شما حذف شد.')


def send_audio_of_poem(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    poem_number = int(query.data.removeprefix('audio'))

    context.bot.forward_message(
        chat_id=update.effective_chat.id,
        from_chat_id=DATABASE_CHANNEL_USERNAME,
        message_id=poem_number + 2   # channel message ID's start from 2
    )

    query.answer()


def display_related_songs_to_poem(update: Update, _: CallbackContext) -> None:
    query = update.callback_query
    poem_number = int(query.data.removeprefix('songs'))

    related_songs: list[dict[str, str]] = poems_info[poem_number]['relatedSongs']

    keyboard = [
        *map(
            lambda song: [InlineKeyboardButton(song['title'], url=song['link'])],
            related_songs
        ),
        [InlineKeyboardButton('بازگشت 🔙', callback_data=f'back{poem_number}')]
    ]

    query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
    query.answer()


def return_to_menu_of_poem(update: Update, _:CallbackContext) -> None:
    query = update.callback_query
    poem_number = int(query.data.removeprefix('back'))
    user = update.effective_user

    keyboard = get_poem_keyboard(poem_number, poems[poem_number], user, update.effective_chat == None)
    query.edit_message_reply_markup(keyboard)
    query.answer()


################################
# InlineQueryHandler callbacks #
################################

def handle_favorite_poems_inline_query(update: Update, _: CallbackContext) -> None:
    user = update.effective_user
    favorite_poems = _user_to_favorite_poems.get(user)

    if not favorite_poems:
        update.inline_query.answer(
            results=[],
            switch_pm_text='لیست علاقه‌مندی‌های شما خالی است ❗️',
            switch_pm_parameter='no-favorite-poems',
        )
        return

    results = list(
        starmap(
            lambda poem_number, poem: InlineQueryResultArticle(
                id=str(uuid4()),
                title=poem,
                input_message_content=InputTextMessageContent(poem),
                reply_markup=get_poem_keyboard(poem_number, poem, user, True),
            ),
            favorite_poems
        )
    )

    update.inline_query.answer(results, cache_time=3)


def handle_inline_query(update: Update, _: CallbackContext) -> None:
    query = update.inline_query.query
    user = update.effective_user

    persian_words = r'^[\u0600-\u06FF\s]+$'
    search_results = []
    if match(_SURROUNDED_WITH_DOUBLE_QUOTES, query):
        search_results = find_results(update, query[1:-1])
    elif match(persian_words, query):
        search_results = find_results(update, query.split())

    poem_number, poem = get_random_poem()
    random_poem = InlineQueryResultArticle(
        id=str(uuid4()),
        title='فال',
        input_message_content=InputTextMessageContent(poem),
        reply_markup=get_poem_keyboard(poem_number, poem, user, True),
    )

    if not search_results:
        update.inline_query.answer(
            results=[random_poem],
            switch_pm_text=_NO_MATCH_WAS_FOUND,
            switch_pm_parameter=_INLINE_HELP
        )
        return

    if _user_to_reply_with_line.get(user, True):
        results = [
            random_poem,
            *map(
                lambda search_result: InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=search_result,
                    input_message_content=InputTextMessageContent(search_result),
                ),
                search_results
            ),
        ]
    else:
        results = [
            random_poem,
            *starmap(
                lambda poem_number, poem: InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=poem,
                    input_message_content=InputTextMessageContent(
                        poem + '🎼وزن: ' + poems_info[poem_number]['meter']
                    ),
                    reply_markup=get_poem_keyboard(poem_number, poem, user, True),
                ),
                search_results
            ),
        ]

    update.inline_query.answer(results, cache_time=3, switch_pm_text='راهنما ❓', switch_pm_parameter=_INLINE_HELP)


####################
# Helper functions #
####################

def get_poem_keyboard(poem_number: int, poem: str, user: User, inline: bool) -> InlineKeyboardMarkup:
    if inline:
        audio_button = InlineKeyboardButton(
            text='خوانش 🗣',
            url=f'https://telegram.me/{_BOT_USERNAME}?start={_SEND_AUDIO}{poem_number}'
        )
    else:
        audio_button = InlineKeyboardButton('خوانش 🗣', callback_data=f'audio{poem_number}')

    keyboard = [[audio_button]]

    if poems_info[poem_number]['relatedSongs']:
        related_songs_button = InlineKeyboardButton(
            text='این شعر را چه کسی در کدام آهنگ خوانده است؟ 🎵',
            callback_data=f'songs{poem_number}'
        )
        keyboard.append([related_songs_button])

    if user in _user_to_favorite_poems and (poem_number, poem) in _user_to_favorite_poems[user]:
        keyboard.append(
            [InlineKeyboardButton('حذف از غزل‌های مورد علاقه', callback_data=f'remove{poem_number}')]
        )
    else:
        keyboard.append(
            [InlineKeyboardButton('افزودن به غزل های مورد علاقه ❤️', callback_data=f'add{poem_number}')]
        )

    return InlineKeyboardMarkup(keyboard)


def get_random_poem() -> tuple[int, str]:
    rand = randrange(0, POEMS_COUNT - 1)
    return rand, poems[rand]


def search_impl(update: Update, to_search: Union[str, list[str]]) -> None:
    user = update.effective_user

    def send_results() -> None:
        results = find_results(update, to_search)
        if not results:
            update.message.reply_text(_NO_MATCH_WAS_FOUND)
        elif _user_to_reply_with_line[user]:
            for poem in results:
                update.message.reply_text(poem)
        else:
            for poem_number, poem in results:
                meter = '🎼وزن: ' + poems_info[poem_number]['meter']
                update.message.reply_text(
                    text=poem + meter,
                    reply_markup=get_poem_keyboard(poem_number, poem, user, False)
                )

    if user not in _user_to_reply_with_line:
        choose_result_mode(update)
        global _to_invoke
        _to_invoke = send_results
    else:
        send_results()


def find_results(update: Update, to_search: Union[str, list[str]]) -> Union[list[str], list[tuple[int, str]]]:
    index_of_matched_line = index_of_matched_line_string if isinstance(to_search, str) else index_of_matched_line_words
    if _user_to_reply_with_line.get(update.effective_user, True):
        results = _searcher.search_return_lines(to_search, index_of_matched_line)
    else:
        results = _searcher.search_return_poems(to_search, index_of_matched_line)

    return results


def choose_result_mode(update: Update) -> None:
    keyboard = [
        [InlineKeyboardButton('در نتیجه جستجو، کل غزل دریافت شود.', callback_data='poem')],
        [InlineKeyboardButton('در نتیجه جستجو، فقط بیت دریافت شود.', callback_data='line')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text('لطفا انتخاب کن:', reply_markup=reply_markup)


def main() -> None:
    updater = Updater(API_TOKEN)
    dispatcher = updater.dispatcher

    global _BOT_USERNAME
    _BOT_USERNAME = dispatcher.bot.username

    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(CommandHandler('help', help_command))
    dispatcher.add_handler(CommandHandler('fal', random_poem_command))
    dispatcher.add_handler(CommandHandler('ghazal', reply_poem))
    dispatcher.add_handler(CommandHandler('beit', reply_line))
    dispatcher.add_handler(CommandHandler('favorite', list_favorite_poems))

    dispatcher.add_handler(MessageHandler(Filters.regex(_SURROUNDED_WITH_DOUBLE_QUOTES), search_string))
    dispatcher.add_handler(
        MessageHandler(
            Filters.text & ~Filters.command & ~Filters.via_bot(username=_BOT_USERNAME),
            search_words,
        )
    )

    dispatcher.add_handler(CallbackQueryHandler(result_mode_chosen, pattern=r'^(poem|line)$'))
    dispatcher.add_handler(CallbackQueryHandler(add_to_favorite_poems, pattern=r'^add\d{1,3}$'))
    dispatcher.add_handler(CallbackQueryHandler(remove_from_favorite_poems, pattern=r'^remove\d{1,3}$'))
    dispatcher.add_handler(CallbackQueryHandler(send_audio_of_poem, pattern=r'^audio\d{1,3}$'))
    dispatcher.add_handler(CallbackQueryHandler(display_related_songs_to_poem, pattern=r'^songs\d{1,3}$'))
    dispatcher.add_handler(CallbackQueryHandler(return_to_menu_of_poem, pattern=r'^back\d{1,3}$'))

    dispatcher.add_handler(InlineQueryHandler(handle_favorite_poems_inline_query, pattern=_FAVORITE_POEMS_QUERY))
    dispatcher.add_handler(InlineQueryHandler(handle_inline_query))

    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
