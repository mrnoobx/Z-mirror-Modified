from calendar import c
from aiohttp import ClientSession
from apscheduler.triggers.interval import IntervalTrigger
from asyncio import Lock, sleep
from datetime import datetime, timedelta
from feedparser import parse as feedparse
from functools import partial
from io import BytesIO
from pyrogram.filters import command, regex, create
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from re import split as re_split, sub as re_sub
from time import time

from bot import scheduler, rss_dict, LOGGER, DATABASE_URL, config_dict, bot
from bot.helper.ext_utils.bot_utils import new_thread
from bot.helper.ext_utils.db_handler import DbManager
from bot.helper.ext_utils.exceptions import RssShutdownException
from bot.helper.ext_utils.help_messages import RSS_HELP_MESSAGE
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import (
    sendMessage,
    editMessage,
    sendRss,
    sendFile,
    deleteMessage,
)

rss_dict_lock = Lock()
handler_dict = {}


async def rssMenu(event):
    user_id = event.from_user.id
    buttons = ButtonMaker()
    buttons.ibutton("Subscribe", f"rss sub {user_id}")
    buttons.ibutton("Subscriptions", f"rss list {user_id} 0")
    buttons.ibutton("Get Items", f"rss get {user_id}")
    buttons.ibutton("Edit", f"rss edit {user_id}")
    buttons.ibutton("Pause", f"rss pause {user_id}")
    buttons.ibutton("Resume", f"rss resume {user_id}")
    buttons.ibutton("Unsubscribe", f"rss unsubscribe {user_id}")
    if await CustomFilters.sudo("", event):
        buttons.ibutton("All Subscriptions", f"rss listall {user_id} 0")
        buttons.ibutton("Pause All", f"rss allpause {user_id}")
        buttons.ibutton("Resume All", f"rss allresume {user_id}")
        buttons.ibutton("Unsubscribe All", f"rss allunsub {user_id}")
        buttons.ibutton("Delete User", f"rss deluser {user_id}")
        if scheduler.running:
            buttons.ibutton("Shutdown Rss", f"rss shutdown {user_id}")
        else:
            buttons.ibutton("Start Rss", f"rss start {user_id}")
    buttons.ibutton("Close", f"rss close {user_id}")
    button = buttons.build_menu(2)
    msg = f"Rss Menu | Users: {len(rss_dict)} | Running: {scheduler.running}"
    return msg, button


async def updateRssMenu(query):
    msg, button = await rssMenu(query)
    await editMessage(query.message, msg, button)


async def getRssMenu(_, message):
    msg, button = await rssMenu(message)
    await sendMessage(message, msg, button)


async def rssSub(_, message, pre_event):
    user_id = message.from_user.id
    handler_dict[user_id] = False
    if username := message.from_user.username:
        tag = f"@{username}"
    else:
        tag = message.from_user.mention
    msg = ""
    items = message.text.split("\n")
    for index, item in enumerate(items, start=1):
        args = item.split()
        if len(args) < 2:
            await sendMessage(
                message,
                f"{item}. Wrong Input format. Read help message before adding new subcription!",
            )
            continue
        title = args[0].strip()
        if (user_feeds := rss_dict.get(user_id, False)) and title in user_feeds:
            await sendMessage(
                message, f"This title {title} already subscribed! Choose another title!"
            )
            continue
        feed_link = args[1].strip()
        if feed_link.startswith(("-inf", "-exf", "-c")):
            await sendMessage(
                message,
                f"Wrong input in line {index}! Add Title! Read the example!",
            )
            continue
        inf_lists = []
        exf_lists = []
        if len(args) > 2:
            arg = item.split(" -c ", 1)
            cmd = re_split(" -inf | -exf ", arg[1])[0].strip() if len(arg) > 1 else None
            arg = item.split(" -inf ", 1)
            inf = re_split(" -c | -exf ", arg[1])[0].strip() if len(arg) > 1 else None
            arg = item.split(" -exf ", 1)
            exf = re_split(" -c | -inf ", arg[1])[0].strip() if len(arg) > 1 else None
            if inf is not None:
                filters_list = inf.split("|")
                for x in filters_list:
                    y = x.split(" or ")
                    inf_lists.append(y)
            if exf is not None:
                filters_list = exf.split("|")
                for x in filters_list:
                    y = x.split(" or ")
                    exf_lists.append(y)
        else:
            inf = None
            exf = None
            cmd = None
        try:
            async with ClientSession() as session:
                async with session.get(feed_link, ssl=False) as res:
                    html = await res.text()
            rss_d = feedparse(html)
            last_title = rss_d.entries[0]["title"]
            msg += "<b>Subscribed!</b>"
            msg += f"\n<b>Title: </b><code>{title}</code>\n<b>Feed Url: </b>{feed_link}"
            msg += f"\n<b>latest record for </b>{rss_d.feed.title}:"
            msg += (
                f"\nName: <code>{last_title.replace('>', '').replace('<', '')}</code>"
            )
            try:
                last_link = rss_d.entries[0]["links"][1]["href"]
            except IndexError:
                last_link = rss_d.entries[0]["link"]
            msg += f"\nLink: <code>{last_link}</code>"
            msg += f"\n<b>Command: </b><code>{cmd}</code>"
            msg += (
                f"\n<b>Filters:-</b>\ninf: <code>{inf}</code>\nexf: <code>{exf}</code>"
            )
            async with rss_dict_lock:
                if rss_dict.get(user_id, False):
                    rss_dict[user_id][title] = {
                        "link": feed_link,
                        "last_feed": last_link,
                        "last_title": last_title,
                        "inf": inf_lists,
                        "exf": exf_lists,
                        "paused": False,
                        "command": cmd,
                        "tag": tag,
                    }
                else:
                    rss_dict[user_id] = {
                        title: {
                            "link": feed_link,
                            "last_feed": last_link,
                            "last_title": last_title,
                            "inf": inf_lists,
                            "exf": exf_lists,
                            "paused": False,
                            "command": cmd,
                            "tag": tag,
                        }
                    }
            LOGGER.info(
                f"Rss Feed Added: id: {user_id} - title: {title} - link: {feed_link} - c: {cmd} - inf: {inf} - exf: {exf}"
            )
        except (IndexError, AttributeError) as e:
            emsg = f"The link: {feed_link} doesn't seem to be a RSS feed or it's region-blocked!"
            await sendMessage(message, emsg + "\nError: " + str(e))
        except Exception as e:
            await sendMessage(message, str(e))
    if msg:
        if DATABASE_URL and rss_dict[user_id]:
            await DbManager().rss_update(user_id)
        await sendMessage(message, msg)
        is_sudo = await CustomFilters.sudo("", message)
        if scheduler.state == 2:
            scheduler.resume()
        elif is_sudo and not scheduler.running:
            addJob()
            scheduler.start()
    await updateRssMenu(pre_event)


async def getUserId(title):
    async with rss_dict_lock:
        return next(
            (
                (True, user_id)
                for user_id, feed in list(rss_dict.items())
                if feed["title"] == title
            ),
            (False, False),
        )


async def rssUpdate(_, message, pre_event, state):
    user_id = message.from_user.id
    handler_dict[user_id] = False
    titles = message.text.split()
    is_sudo = await CustomFilters.sudo("", message)
    updated = []
    for title in titles:
        title = title.strip()
        if not (res := rss_dict[user_id].get(title, False)):
            if is_sudo:
                res, user_id = await getUserId(title)
            if not res:
                user_id = message.from_user.id
                await sendMessage(message, f"{title} not found!")
                continue
        istate = rss_dict[user_id][title].get("paused", False)
        if istate and state == "pause" or not istate and state == "resume":
            await sendMessage(message, f"{title} already {state}d!")
            continue
        async with rss_dict_lock:
            updated.append(title)
            if state == "unsubscribe":
                del rss_dict[user_id][title]
            elif state == "pause":
                rss_dict[user_id][title]["paused"] = True
            elif state == "resume":
                rss_dict[user_id][title]["paused"] = False
        if state == "resume":
            if scheduler.state == 2:
                scheduler.resume()
            elif is_sudo and not scheduler.running:
                addJob()
                scheduler.start()
        if is_sudo and DATABASE_URL and user_id != message.from_user.id:
            await DbManager().rss_update(user_id)
        if not rss_dict[user_id]:
            async with rss_dict_lock:
                del rss_dict[user_id]
            if DATABASE_URL:
                await DbManager().rss_delete(user_id)
                if not rss_dict:
                    await DbManager().trunc_table("rss")
    if updated:
        LOGGER.info(f"Rss link with Title(s): {updated} has been {state}d!")
        await sendMessage(
            message,
            f"Rss links with Title(s): <code>{updated}</code> has been {state}d!",
        )
        if DATABASE_URL and rss_dict.get(user_id):
            await DbManager().rss_update(user_id)
    await updateRssMenu(pre_event)


async def rssList(query, start, all_users=False):
    user_id = query.from_user.id
    buttons = ButtonMaker()
    if all_users:
        list_feed = f"<b>All subscriptions | Page: {int(start / 5)} </b>"
        async with rss_dict_lock:
            keysCount = sum(len(v.keys()) for v in list(rss_dict.values()))
            index = 0
            for titles in list(rss_dict.values()):
                for index, (title, data) in enumerate(
                    list(titles.items())[start : 5 + start]
                ):
                    list_feed += f"\n\n<b>Title:</b> <code>{title}</code>\n"
                    list_feed += f"<b>Feed Url:</b> <code>{data['link']}</code>\n"
                    list_feed += f"<b>Command:</b> <code>{data['command']}</code>\n"
                    list_feed += f"<b>Inf:</b> <code>{data['inf']}</code>\n"
                    list_feed += f"<b>Exf:</b> <code>{data['exf']}</code>\n"
                    list_feed += f"<b>Paused:</b> <code>{data['paused']}</code>\n"
                    list_feed += f"<b>User:</b> {data['tag'].replace('@', '', 1)}"
                    index += 1
                    if index == 5:
                        break
    else:
        list_feed = f"<b>Your subscriptions | Page: {int(start / 5)} </b>"
        async with rss_dict_lock:
            keysCount = len(rss_dict.get(user_id, {}).keys())
            for title, data in list(rss_dict[user_id].items())[start : 5 + start]:
                list_feed += f"\n\n<b>Title:</b> <code>{title}</code>\n<b>Feed Url: </b><code>{data['link']}</code>\n"
                list_feed += f"<b>Command:</b> <code>{data['command']}</code>\n"
                list_feed += f"<b>Inf:</b> <code>{data['inf']}</code>\n"
                list_feed += f"<b>Exf:</b> <code>{data['exf']}</code>\n"
                list_feed += f"<b>Paused:</b> <code>{data['paused']}</code>\n"
    buttons.ibutton("Back", f"rss back {user_id}")
    buttons.ibutton("Close", f"rss close {user_id}")
    if keysCount > 5:
        for x in range(0, keysCount, 5):
            buttons.ibutton(
                f"{int(x / 5)}", f"rss list {user_id} {x}", position="footer"
            )
    button = buttons.build_menu(2)
    if query.message.text.html == list_feed:
        return
    await editMessage(query.message, list_feed, button)


async def rssGet(_, message, pre_event):
    user_id = message.from_user.id
    handler_dict[user_id] = False
    args = message.text.split()
    if len(args) < 2:
        await sendMessage(
            message,
            f"{args}. Wrong Input format. You should add number of the items you want to get. Read help message before adding new subcription!",
        )
        await updateRssMenu(pre_event)
        return
    try:
        title = args[0]
        count = int(args[1])
        data = rss_dict[user_id].get(title, False)
        if data and count > 0:
            try:
                msg = await sendMessage(
                    message, f"Getting the last <b>{count}</b> item(s) from {title}"
                )
                async with ClientSession() as session:
                    async with session.get(data["link"], ssl=False) as res:
                        html = await res.text()
                rss_d = feedparse(html)
                item_info = ""
                for item_num in range(count):
                    try:
                        link = rss_d.entries[item_num]["links"][1]["href"]
                    except IndexError:
                        link = rss_d.entries[item_num]["link"]
                    item_info += f"<b>Name: </b><code>{rss_d.entries[item_num]['title'].replace('>', '').replace('<', '')}</code>\n"
                    item_info += f"<b>Link: </b><code>{link}</code>\n\n"
                item_info_ecd = item_info.encode()
                if len(item_info_ecd) > 4000:
                    with BytesIO(item_info_ecd) as out_file:
                        out_file.name = f"rssGet {title} items_no. {count}.txt"
                        await sendFile(message, out_file)
                    await deleteMessage(msg)
                else:
                    await editMessage(msg, item_info)
            except IndexError as e:
                LOGGER.error(str(e))
                await editMessage(
                    msg, "Parse depth exceeded. Try again with a lower value."
                )
            except Exception as e:
                LOGGER.error(str(e))
                await editMessage(msg, str(e))
        else:
            await sendMessage(message, "Enter a valid title. Title not found!")
    except Exception as e:
        LOGGER.error(str(e))
        await sendMessage(message, f"Enter a valid value!. {e}")
    await updateRssMenu(pre_event)


async def rssEdit(_, message, pre_event):
    user_id = message.from_user.id
    handler_dict[user_id] = False
    items = message.text.split("\n")
    updated = False
    for item in items:
        args = item.split()
        title = args[0].strip()
        if len(args) < 2:
            await sendMessage(
                message,
                f"{item}. Wrong Input format. Read help message before editing!",
            )
            continue
        elif not rss_dict[user_id].get(title, False):
            await sendMessage(message, "Enter a valid title. Title not found!")
            continue
        updated = True
        inf_lists = []
        exf_lists = []
        arg = item.split(" -c ", 1)
        cmd = re_split(" -inf | -exf ", arg[1])[0].strip() if len(arg) > 1 else None
        arg = item.split(" -inf ", 1)
        inf = re_split(" -c | -exf ", arg[1])[0].strip() if len(arg) > 1 else None
        arg = item.split(" -exf ", 1)
        exf = re_split(" -c | -inf ", arg[1])[0].strip() if len(arg) > 1 else None
        async with rss_dict_lock:
            if cmd is not None:
                if cmd.lower() == "none":
                    cmd = None
                rss_dict[user_id][title]["command"] = cmd
            if inf is not None:
                if inf.lower() != "none":
                    filters_list = inf.split("|")
                    for x in filters_list:
                        y = x.split(" or ")
                        inf_lists.append(y)
                rss_dict[user_id][title]["inf"] = inf_lists
            if exf is not None:
                if exf.lower() != "none":
                    filters_list = exf.split("|")
                    for x in filters_list:
                        y = x.split(" or ")
                        exf_lists.append(y)
                rss_dict[user_id][title]["exf"] = exf_lists
    if DATABASE_URL and updated:
        await DbManager().rss_update(user_id)
    await updateRssMenu(pre_event)


async def rssDelete(_, message, pre_event):
    handler_dict[message.from_user.id] = False
    users = message.text.split()
    for user in users:
        user = int(user)
        async with rss_dict_lock:
            del rss_dict[user]
        if DATABASE_URL:
            await DbManager().rss_delete(user)
    await updateRssMenu(pre_event)


async def event_handler(client, query, pfunc):
    user_id = query.from_user.id
    handler_dict[user_id] = True
    start_time = time()

    async def event_filter(_, __, event):
        user = event.from_user or event.sender_chat
        return bool(
            user.id == user_id and event.chat.id == query.message.chat.id and event.text
        )

    handler = client.add_handler(MessageHandler(pfunc, create(event_filter)), group=-1)
    while handler_dict[user_id]:
        await sleep(0.5)
        if time() - start_time > 60:
            handler_dict[user_id] = False
            await updateRssMenu(query)
    client.remove_handler(*handler)


@new_thread
async def rssListener(client, query):
    user_id = query.from_user.id
    message = query.message
    data = query.data.split()
    if int(data[2]) != user_id and not await CustomFilters.sudo("", query):
        await query.answer(
            text="You don't have permission to use these buttons!", show_alert=True
        )
    elif data[1] == "close":
        await query.answer()
        handler_dict[user_id] = False
        await deleteMessage(message.reply_to_message)
        await deleteMessage(message)
    elif data[1] == "back":
        await query.answer()
        handler_dict[user_id] = False
        await updateRssMenu(query)
    elif data[1] == "sub":
        await query.answer()
        handler_dict[user_id] = False
        buttons = ButtonMaker()
        buttons.ibutton("Back", f"rss back {user_id}")
        buttons.ibutton("Close", f"rss close {user_id}")
        button = buttons.build_menu(2)
        await editMessage(message, RSS_HELP_MESSAGE, button)
        pfunc = partial(rssSub, pre_event=query)
        await event_handler(client, query, pfunc)
    elif data[1] == "list":
        handler_dict[user_id] = False
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(text="No subscriptions!", show_alert=True)
        else:
            await query.answer()
            start = int(data[3])
            await rssList(query, start)
    elif data[1] == "get":
        handler_dict[user_id] = False
        if len(rss_dict.get(int(data[2]), {})) == 0:
            await query.answer(text="No subscriptions!", show_alert=True)
        else:
            await query.answer()
            buttons = ButtonMaker()
            buttons.ibutton("Back", f"rss back {user_id}")
            buttons.ibutton("Close", f"rss close {user_id}")
            button = buttons.build_menu(2)
            await editMessage(
                message,
                "Send one title with value separated by space get last X items.\nTitle Value\nTimeout: 60 sec.",
                button,
            )
            pfunc = partial(rssGet, pre_event=query)
            await event_handler(client, query, pfunc)
    elif data[1] in ["unsubscribe", "pause", "resume"]:
        handler_dict[user_id] = False
        if len(rss_dict.get(int(data[2]), {})) == 0:
            
