import os
import tempfile
import asyncio
import gettext
import re
import pickle
import json
import time
import datetime
from time import strftime
import logging
import discord
from discord.ext import commands
import spelling



formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
def setup_logger(name, log_file, level):
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    handler = logging.FileHandler(log_file, encoding='utf-8', mode='w')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

try:
    os.makedirs("logs")
except OSError as exception:
    pass
    
logger = setup_logger('discord','logs/meowth.log',logging.INFO)

Meowth = commands.Bot(command_prefix="!")

with open("serverdict", "rb") as fd:
    server_dict = pickle.load(fd)

config = {}
pkmn_info = {}
type_chart = {}
zone_list = {}
type_list = []
active_raids = []

# Append path of this script to the path of
# config files which we're loading.
# Assumes that config files will always live in the same directory.
script_path = os.path.dirname(os.path.realpath(__file__))

def load_config():
    global config
    global pkmn_info
    global zone_list
    global type_chart
    global type_list

    # Load configuration
    with open(os.path.join(script_path, "config.json"), "r") as fd:
        config = json.load(fd)

    # Set up message catalog access
    language = gettext.translation('meowth', localedir='locale', languages=[config['bot-language']])
    language.install()
    pokemon_language = [config['pokemon-language']]
    pokemon_path_source = "locale/{0}/pkmn.json".format(config['pokemon-language'])
    zone_path_source = "locale/{0}/zone.json"

    # Load Pokemon list and raid info
    with open(os.path.join(script_path, pokemon_path_source), "r") as fd:
        pkmn_info = json.load(fd)
        
    # Load zone role list
    with open(os.path.join(script_path, zone_path_source), "r") as fd:
        zone_list = json.load(fd)

    # Load type information
    with open(os.path.join(script_path, "type_chart.json"), "r") as fd:
        type_chart = json.load(fd)
    with open(os.path.join(script_path, "type_list.json"), "r") as fd:
        type_list = json.load(fd)

    # Set spelling dictionary to our list of Pokemon
    spelling.set_dictionary(pkmn_info['pokemon_list'])

load_config()



"""

======================

Helper functions

======================

"""

# Given a Pokemon name, return a list of its
# weaknesses as defined in the type chart
def get_weaknesses(species):
    # Get the Pokemon's number
    number = pkmn_info['pokemon_list'].index(species)
    # Look up its type
    pk_type = type_list[number]

    # Calculate sum of its weaknesses
    # and resistances.
    # -2 == immune
    # -1 == NVE
    #  0 == neutral
    #  1 == SE
    #  2 == double SE
    type_eff = {}
    for type in pk_type:
        for atk_type in type_chart[type]:
            if atk_type not in type_eff:
                type_eff[atk_type] = 0
            type_eff[atk_type] += type_chart[type][atk_type]

    # Summarize into a list of weaknesses,
    # sorting double weaknesses to the front and marking them with 'x2'.
    ret = []
    for type, effectiveness in sorted(type_eff.items(), key=lambda x: x[1], reverse=True):
        if effectiveness == 1:
            ret.append(type.lower())
        elif effectiveness == 2:
            ret.append(type.lower() + "x2")

    return ret


# Given a list of weaknesses, return a
# space-separated string of their type IDs,
# as defined in the type_id_dict
def weakness_to_str(server, weak_list):
    ret = ""
    for weakness in weak_list:
        # Handle an "x2" postfix defining a double weakness
        x2 = ""
        if weakness[-2:] == "x2":
            weakness = weakness[:-2]
            x2 = "x2"

        # Append to string
        ret += parse_emoji(server, config['type_id_dict'][weakness]) + x2 + " "

    return ret


# Convert an arbitrary string into something which
# is acceptable as a Discord channel name.
def sanitize_channel_name(name):
    # Remove all characters other than alphanumerics,
    # dashes, underscores, and spaces
    ret = re.sub(r"[^a-zA-Z0-9 _\-]", "", name)
    # Replace spaces with dashes
    ret = ret.replace(" ", "-")

    return ret

# Given a string, if it fits the pattern :emoji name:,
# and <emoji_name> is in the server's emoji list, then
# return the string <:emoji name:emoji id>. Otherwise,
# just return the string unmodified.
def parse_emoji(server, emoji_string):
    if emoji_string[0] == ':' and emoji_string[-1] == ':':
        emoji = discord.utils.get(server.emojis, name=emoji_string.strip(':'))
        if emoji:
            emoji_string = "<:{0}:{1}>".format(emoji.name, emoji.id)
        else:
            emoji_string = "{0}".format(emoji_string.strip(':').capitalize())

    return emoji_string

def print_emoji_name(server, emoji_string):
    # By default, just print the emoji_string
    ret = "`" + emoji_string + "`"

    emoji = parse_emoji(server, emoji_string)
    # If the string was transformed by the parse_emoji
    # call, then it really was an emoji and we should
    # add the raw string so people know what to write.
    if emoji != emoji_string:
        ret = emoji + " (`" + emoji_string + "`)"

    return ret

# Given an arbitrary string, create a Google Maps
# query using the configured hints
def create_gmaps_query(details, channel):
    details_list = details.split()
    loc_list = server_dict[channel.server]['city_channels'][channel.name].split()
    return "https://www.google.com/maps/search/?api=1&query={0}+{1}".format('+'.join(details_list),'+'.join(loc_list))

# Given a User, check that it is Meowth's master
def check_master(user):
    return str(user) == config['master']

def check_server_owner(user, server):
    return str(user) == str(server.owner)

# Given a violating message, raise an exception
# reporting unauthorized use of admin commands
def raise_admin_violation(message):
    raise Exception(_("Received admin command {command} from unauthorized user, {user}!").format(command=message.content, user=message.author))

def spellcheck(word):
    suggestion = spelling.correction(word)

    # If we have a spellcheck suggestion
    if suggestion != word:
        return _("Meowth! \"{entered_word}\" is not a Pokemon! Did you mean \"{corrected_word}\"?").format(entered_word=word, corrected_word=spelling.correction(word))
    else:
        return _("Meowth! \"{entered_word}\" is not a Pokemon! Check your spelling!").format(entered_word=word)



async def expiry_check(channel):
    logger.info("Expiry_Check - "+channel.name)
    server = channel.server
    global active_raids
    if channel not in active_raids:
        active_raids.append(channel)
        logger.info("Expire_Channel - Channel Added To Watchlist - "+channel.name)
        while True:
            try:
                if server_dict[server]['raidchannel_dict'][channel]['active'] is True:
                    if server_dict[server]['raidchannel_dict'][channel]['exp'] <= time.time():
                        event_loop.create_task(expire_channel(channel))
                        active_raids.remove(channel)
                        logger.info("Expire_Channel - Channel Expired And Removed From Watchlist - "+channel.name)
                        break
            except KeyError:
                pass
            
            await asyncio.sleep(30)
            continue

async def expire_channel(channel):
    server = channel.server
    logger.info("Expire_Channel - "+channel.name)
    # If the channel exists, get ready to delete it.
    # Otherwise, just clean up the dict since someone
    # else deleted the actual channel at some point.
    
    channel_exists = Meowth.get_channel(channel.id)
    if channel_exists is None:
        try:
            del server_dict[channel.server]['raidchannel_dict'][channel]
        except KeyError:
            pass
        return
    else:
        server_dict[server]['raidchannel_dict'][channel]['active'] = False
        logger.info("Expire_Channel - Channel Expired - "+channel.name)
        await Meowth.send_message(channel, _("""This channel timer has expired! The channel has been deactivated and will be deleted in 5 minutes.
To reactivate the channel, use !timerset to set the timer again."""))
        delete_time = server_dict[server]['raidchannel_dict'][channel]['exp'] + (5 * 60) - time.time()
        await asyncio.sleep(delete_time)
        # If the channel has already been deleted from the dict, someone
        # else got to it before us, so don't do anything.
        # Also, if the channel got reactivated, don't do anything either.
            
        try:
            if server_dict[channel.server]['raidchannel_dict'][channel]['active'] == False:
                try:
                    del server_dict[channel.server]['raidchannel_dict'][channel]
                except KeyError:
                    pass
                    #channel doesn't exist anymore in serverdict
                channel_exists = Meowth.get_channel(channel.id)
                if channel_exists is None:
                    return
                else:
                    await Meowth.delete_channel(channel)
                    logger.info("Expire_Channel - Channel Deleted - "+channel.name)
        except:
            pass


async def channel_cleanup(loop=False):
    while True:
        global active_raids
        serverdict_temp = server_dict
        logger.info("Channel_Cleanup ------ BEGIN ------")
        
        #for every server in save data
        for server in serverdict_temp.keys():
            
            log_str = "Channel_Cleanup - Server: "+server.name
            logger.info(log_str+" - BEGIN CHECKING SERVER")
            
            #clear channel lists
            dict_channel_delete = []
            discord_channel_delete =[]
            dict_expired_channel_list = []
            
            #check every raid channel data for each server
            for channel in serverdict_temp[server]['raidchannel_dict']:
            
                log_str = log_str+": Channel:"+channel.name
                logger.info(log_str+" - CHECKING")
            
                #does meowth see the channel still in discord?
                channel_exists = Meowth.get_channel(channel.id)
                
                #if meowth can't see it exists in discord
                if channel_exists is None:
                
                    #list channel for deletion from save data
                    dict_channel_delete.append(channel)
                    logger.info(log_str+" - DOESN'T EXIST IN DISCORD")
                    
                #otherwise, if meowth can still see the channel in discord
                else:
                    
                    #if the channel save data shows it's not an active raid
                    if serverdict_temp[server]['raidchannel_dict'][channel]['active'] == False:
                        
                        #and if it has been expired for longer than 5 minutes already
                        if serverdict_temp[server]['raidchannel_dict'][channel]['exp'] < (time.time() - (5 * 60)):
                            
                            #list the channel to be removed from save data
                            dict_channel_delete.append(channel)
                            
                            #and list the channel to be deleted in discord
                            discord_channel_delete(channel)
                            
                            logger.info(log_str+" - 5+ MIN EXPIRY NONACTIVE")
                    
                    #if the channel save data shows it as an active raid still
                    elif serverdict_temp[server]['raidchannel_dict'][channel]['active'] == True:
                        
                        #and if it has been expired for longer than 5 minutes already
                        if serverdict_temp[server]['raidchannel_dict'][channel]['exp'] < (time.time() - (5 * 60)):
                            
                            #list the channel to be removed from save data
                            dict_channel_delete.append(channel)
                            
                            #and list the channel to be deleted in discord
                            discord_channel_delete(channel)
                            
                            logger.info(log_str+" - 5+ MIN EXPIRY ACTIVE")
                    
                        #or if the expiry time for the channel has already passed within 5 minutes
                        elif serverdict_temp[server]['raidchannel_dict'][channel]['exp'] <= time.time():
                        
                            #mark the raid channel as no longer active in raid data
                            serverdict_temp[channel.server]['raidchannel_dict'][channel]['active'] = False
                            
                            #and list the channel to be sent to the channel expiry function
                            dict_expired_channel_list.append(channel)
                            
                            logger.info(log_str+" - RECENTLY EXPIRED")
                            
                        else:
                            #if channel is still active, make sure it's expiry is being monitored
                            if channel not in active_raids:
                                await expiry_check(channel)
                                logger.info(log_str+" - MISSING FROM EXPIRY CHECK")
                
                log_str = "Channel_Cleanup - Server: "+server.name
            
            #for every channel listed to have save data deleted
            for c in dict_channel_delete:
                try:
                    #attempt to delete the channel from save data
                    del server_dict[server]['raidchannel_dict'][c]
                    
                    #and check if the channel is still in discord
                    channel_exists = Meowth.get_channel(c.id)
                    
                    if channel_exists is not None:
                    
                        #delete channel if it still exists in discord
                        Meowth.delete_channel(c)
                        
                except KeyError:
                    #if keyerror, then the channel is no longer in savedata anyway
                    continue
            
            #for every channel listed to have the discord channel deleted
            for c in discord_channel_delete:
                
                #check if meowth can see it exists in discord
                if channel_exists is not None:
                
                    #delete channel from discord
                    await Meowth.delete_channel(channel)
            
            #for every channel listed to have recently expired
            for e in dict_expired_channel_list:
                await expire_channel(e)
        
        #save server_dict changes after cleanup
        logger.info("Channel_Cleanup - SAVING CHANGES")
        await _save()
        logger.info("Channel_Cleanup ------ END ------")
        
        await asyncio.sleep(600)
        continue

async def server_cleanup(loop=False):
    while True:
        serverdict_temp = server_dict
        logger.info("Server_Cleanup ------ BEGIN ------")

        serverdict_temp = server_dict
        dict_server_list = []
        bot_server_list = []
        dict_server_delete = []
        
        for server in serverdict_temp.keys():
            dict_server_list.append(server)
        for server in Meowth.servers:
            bot_server_list.append(server)
        server_diff = set(dict_server_list) - set(bot_server_list)
        for s in server_diff:
            dict_server_delete.append(s)
        
        for s in dict_server_delete:
            try:
                del server_dict[s]
                logger.info("Server_Cleanup - Cleared "+s.name+" from save data")
            except KeyError:
                pass
        
        logger.info("Server_Cleanup - SAVING CHANGES")
        await _save()
        logger.info("Server_Cleanup ------ END ------")
        await asyncio.sleep(1800)
        continue


def maint_start():
    try:
        event_loop.create_task(server_cleanup())
        event_loop.create_task(channel_cleanup())
    except KeyboardInterrupt as e:
        tasks.cancel()
    
event_loop = asyncio.get_event_loop()

"""

======================

End helper functions

======================

"""


"""
Meowth tracks raiding commands through the raidchannel_dict.
Each channel contains the following fields:
'trainer_dict' : a dictionary of all trainers interested in the raid.
'exp'          : an instance of time.struct_time tracking when the raid ends.
'active'       : a Boolean indicating whether the raid is still active.

The trainer_dict contains "trainer" elements, which have the following fields:
'status' : a string indicating either "omw" or "waiting"
'count'  : the number of trainers in the party
"""

team_msg = " or ".join(["'!team {0}'".format(team) for team in config['team_dict'].keys()])


@Meowth.event
async def on_ready():
    print(_("Meowth! That's right!")) #prints to the terminal or cmd prompt window upon successful connection to Discord
    for server in Meowth.servers:
        try:
            if server not in server_dict:
                server_dict[server] = {'want_channel_list': [], 'offset': 0, 'welcome': False, 'team': False, 'want': False, 'other': False, 'done': False, 'raidchannel_dict' : {}}
        except KeyError:
            server_dict[server] = {'want_channel_list': [], 'offset': 0, 'welcome': False, 'team': False, 'want': False, 'other': False, 'done': False, 'raidchannel_dict' : {}} 
        
    await channel_cleanup()


@Meowth.event
async def on_server_join(server):
    owner = server.owner
    server_dict[server] = {'want_channel_list': [], 'offset': 0, 'welcome': False, 'team': False, 'want': False, 'other': False, 'done': False, 'raidchannel_dict' : {}}
    await Meowth.send_message(owner, _("Meowth! I'm Meowth, a Discord helper bot for Pokemon Go communities, and someone has invited me to your server! Type !help to see a list of things I can do, and type !configure in any channel of your server to begin!"))

@Meowth.event
async def on_server_remove(server):
    try:
        if server in server_dict[server]:
            try:
                del server_dict[server]
            except KeyError:
                pass
    except KeyError:
        pass
        
@Meowth.command(pass_context=True, hidden=True)
@commands.has_permissions(manage_server=True)
async def configure(ctx):
    server = ctx.message.server
    owner = ctx.message.author
    server_dict_temp = {'want_channel_list': [], 'offset': 0, 'welcome': False, 'welcomechan': "", 'wantset': False, 'raidset': False, 'wildset': False, 'team': False, 'want': False, 'other': False, 'done': False, 'raidchannel_dict' : {}}
    await Meowth.send_message(owner, _("""__**Meowth Configuration**__\nMeowth! That's Right! Welcome to the configuration for Meowth the Pokemon Go Helper Bot! I will be guiding you through some setup steps to get me setup on your server.\n\n**Role Setup**\nBefore you begin the configuration, please make sure my role is moved to the top end of the server role hierarchy. It can be under admins and mods, but must be above team ands general roles. Here is an example: <http://i.imgur.com/c5eaX1u.png>\n\nReply with **cancel** at any time throughout the questions to cancel the configure process.\n\n**Team Assignments**\nTeam assignment allows users to assign their Pokemon Go team role using the !team command. If you have a bot that handles this already, you may want to disable this feature.\nIf you are to use this feature, ensure existing team roles are as follows: mystic, valor, instinct. These must be all lowercase letters. If they don't exist yet, I'll make some for you instead.\n\nRespond with: **N** to disable, **Y** to enable:"""))
    while True:
        teamreply = await Meowth.wait_for_message(author = owner, check=lambda message: message.server is None)
        if teamreply.content.lower() == "y":
            server_dict_temp['team']=True
            for team in config['team_dict'].keys():
                temp_role = discord.utils.get(server.roles, name=team)
                if temp_role == None:
                    await Meowth.create_role(server = server, name = team, hoist = False, mentionable = True)
            await Meowth.send_message(owner, _("**Team Assignments enabled!**\n---"))
            break
        elif teamreply.content.lower() == "n":
            server_dict_temp['team']=False
            await Meowth.send_message(owner, _("**Team assignments disabled!**\n---"))
            break
        elif teamreply.content.lower == "cancel":
            await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
            return
        else:
            await Meowth.send_message(owner, _("I'm sorry I don't understand. Please reply with either **N** to disable, or **Y** to enable."))
            continue
            
    await Meowth.send_message(owner, _("**Welcome Message**\n\n I can welcome new members to the server with a short message. Here is an example:\n"))
    if server_dict_temp['team'] == True:
        await Meowth.send_message(owner, _("Meowth! Welcome to {server_name}, {owner_name.mention}! Set your team by typing '!team mystic' or '!team valor' or '!team instinct' without quotations. If you have any questions just ask an admin.").format(server_name=server.name, owner_name=owner))
    else:
        await Meowth.send_message(owner, _("Meowth! Welcome to {server_name}, {owner_name.mention}! If you have any questions just ask an admin.").format(server_name=server, owner_name=owner))
    await Meowth.send_message(owner, _("This welcome message can be in a specific channel or a direct message. If you have a bot that handles this already, you may want to disable this feature.\n\nRespond with: **N** to disable, **Y** to enable:"))
    while True:
        welcomereply = await Meowth.wait_for_message(author = owner, check=lambda message: message.server is None)
        if welcomereply.content.lower() == "y":
            server_dict_temp['welcome'] = True
            await Meowth.send_message(owner, _("**Welcome Message enabled!**\n---\n**Welcome Message Channels**\nWhich channel in your server would you like me to post the Welcome Messages? You can also choose to have them sent to the new member via Direct Message (DM) instead.\n\nRespond with: **channel-name** of a channel in your server or **DM** to Direct Message:"))
            wchcheck = 0
            while True:
                welcomechannelreply = await Meowth.wait_for_message(author = owner, check=lambda message: message.server is None)
                if welcomechannelreply.content.lower() == "dm":
                    server_dict_temp['welcomechan'] = "dm"
                    break
                elif " " in welcomechannelreply.content.lower():
                    await Meowth.send_message(owner, _("Channel names can't contain spaces, sorry. Please double check the name and send your response again."))
                    continue
                elif welcomechannelreply.content.lower == "cancel":
                    await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
                    return
                else:
                    server_channel_list = []
                    for channel in server.channels:
                        server_channel_list.append(channel.name)
                    diff = set([welcomechannelreply.content.lower().strip()]) - set(server_channel_list)
                    if not diff:
                        server_dict_temp['welcomechan'] = welcomechannelreply.content.lower()
                        await Meowth.send_message(owner, _("**Welcome Channel set**\n---"))
                        break
                    else:
                        await Meowth.send_message(owner, _("The channel you provided isn't in your server. Please double check your channel name and resend your response."))
                        continue
        elif welcomereply.content.lower() == "n":
            server_dict_temp['welcome'] = False
            await Meowth.send_message(owner, _("**Welcome Message disabled!**\n---"))
            break
        elif welcomereply.content.lower == "cancel":
            await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
            return
        else:
            await Meowth.send_message(owner, _("I'm sorry I don't understand. Please reply with either **N** to disable, or **Y** to enable."))
            continue
        break
    #new function to assign "zones"
    await Meowth.send_message(owner, _("**Zone roles**\nZone roles:\n - If you want to have roles that users can opt-in and out of for different regions\n - **Note** \n - you will need to create the roles and assign permissions as you desire. This option will allow users to manage which roles they have\nIf you don't want __Zones__ want to disable them.\n\nRespond with: **N** to disable, or **Y** to enable:"))
    while True:
        otherreply = await Meowth.wait_for_message(author = owner, check=lambda message: message.server is None)
        if otherreply.content.lower() == "y":
            server_dict_temp['other']=True
            await Meowth.send_message(owner, _("**Zones enabled**\n---\n**Zone List**\n I'll need you to provide a list of roles in your server you will allow users to self-manage in this format: `role, role, role`"))
            
            await Meowth.send_message(owner, _("If you do not require zones, you may want to disable this function.\n\nRespond with: **N** to disable, or the **channel-name** list to enable, each seperated with a comma and space:"))
            zone_dict = {}
            while True:
                zone = await Meowth.wait_for_message(author = owner, check=lambda message: message.server is None)
                if zone.content.lower() == "n":
                    server_dict_temp['zoneset']=False
                    await Meowth.send_message(owner, _("**Reporting Channels disabled**\n---"))
                    break
                elif zone.content.lower() == "cancel":
                    await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
                    return
                else:
                    zone_list = zone.content.lower().split(', ')
                    server_role_list = []
                    for role in server.roles:
                        server_role_list.append(role.name)
                    
                    diff = set(zone_list) - set(server_role_list)
                    if not diff:
                        await Meowth.send_message(owner, _("**Reporting Channels enabled**\n---"))
                    else:
                        await Meowth.send_message(owner, _("The role list you provided doesn't match with your servers roles.\nThe following aren't in your server: {invalid_roles}\nPlease double check your channel list and resend your reponse.").format(invalid_roles=", ".join(diff)))
                        continue
        break    
    await Meowth.send_message(owner, _("**Main Functions**\nMain Functions include:\n - **!want** and creating tracked Pokemon roles \n - **!wild** Pokemon reports\n - **!raid** reports and channel creation for raid management.\nIf you don't want __any__ of the Pokemon tracking or Raid management features, you may want to disable them.\n\nRespond with: **N** to disable, or **Y** to enable:"))
    while True:
        otherreply = await Meowth.wait_for_message(author = owner, check=lambda message: message.server is None)
        if otherreply.content.lower() == "y":
            server_dict_temp['other']=True
            await Meowth.send_message(owner, _("**Main Functions enabled**\n---\n**Reporting Channels**\nPokemon raid or wild reports are contained within one or more channels. Each channel will be able to represent different areas/communities. I'll need you to provide a list of channels in your server you will allow reports from in this format: `channel-name, channel-name, channel-name`"))
            
            await Meowth.send_message(owner, _("If you do not require raid and wild reporting, you may want to disable this function.\n\nRespond with: **N** to disable, or the **channel-name** list to enable, each seperated with a comma and space:"))
            citychannel_dict = {}
            while True:
                citychannels = await Meowth.wait_for_message(author = owner, check=lambda message: message.server is None)
                if citychannels.content.lower() == "n":
                    server_dict_temp['wildset']=False
                    server_dict_temp['raidset']=False
                    await Meowth.send_message(owner, _("**Reporting Channels disabled**\n---"))
                    break
                elif citychannels.content.lower() == "cancel":
                    await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
                    return
                else:
                    citychannel_list = citychannels.content.lower().split(', ')
                    server_channel_list = []
                    for channel in server.channels:
                        server_channel_list.append(channel.name)
                    
                    diff = set(citychannel_list) - set(server_channel_list)
                    if not diff:
                        await Meowth.send_message(owner, _("**Reporting Channels enabled**\n---"))
                    else:
                        await Meowth.send_message(owner, _("The channel list you provided doesn't match with your servers channels.\nThe following aren't in your server: {invalid_channels}\nPlease double check your channel list and resend your reponse.").format(invalid_channels=", ".join(diff)))
                        continue
                        
                await Meowth.send_message(owner, _("""**Report Locations**\nFor each report, I generate Google Maps links to give people directions to raids and spawns! To do this, I need to know which suburb/town/region each report channel represents, to ensure we get the right location in the map. For each report channel you provided, I will need it's corresponding general location using only letters and spaces, with each location seperated by a comma and space.\nExample: `kansas city mo, hull uk, sydney nsw australia`\nEach location will have to be in the same order as you provided the channels in the previous question.\n\nRespond with: **location info, location info, location info** each matching the order of the previous channel list:"""))
                while True:
                    cities = await Meowth.wait_for_message(author=owner, check=lambda message: message.server is None)
                    if cities.content.lower() == "cancel":
                        await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
                        return
                    city_list = cities.content.split(', ')
                    if len(city_list) == len(citychannel_list):
                        for i in range(len(citychannel_list)):
                            citychannel_dict[citychannel_list[i]]=city_list[i]
                        break
                    else:
                        await Meowth.send_message(owner,_("""The number of cities don't match the number of channels you gave me earlier!\nI'll show you the two lists to compare:\n{channellist}\n{citylist}\nPlease double check that your locations match up with your provided channels and resend your response.""").format(channellist=(", ".join(citychannel_list)), citylist=(", ".join(city_list))))
                        continue
                server_dict_temp['city_channels'] = citychannel_dict
                await Meowth.send_message(owner, _("**Report Locations are set**\n---\n**Raid Reports**\nDo you want !raid reports enabled? If you want __only__ the !wild feature for reports, you may want to disable this.\n\nRespond with: **N** to disable, or **Y** to enable:"))
                while True:
                    raidconfigset = await Meowth.wait_for_message(author=owner, check=lambda message: message.server is None)
                    if raidconfigset.content.lower() == "y":
                        server_dict_temp['raidset']=True
                        await Meowth.send_message(owner, _("**Raid Reports enabled**\n---"))
                        break
                    elif raidconfigset.content.lower() == "n":
                        server_dict_temp['raidset']=False
                        await Meowth.send_message(owner, _("**Raid Reports disabled**\n---"))
                        break
                    elif raidconfigset.content.lower() == "cancel":
                        await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
                        return
                    else:
                        await Meowth.send_message(owner, _("I'm sorry I don't understand. Please reply with either **N** to disable, or **Y** to enable."))
                        continue
                await Meowth.send_message(owner, _("**Wild Reports**\nDo you want !wild reports enabled? If you want __only__ the !raid feature for reports, you may want to disable this.\n\nRespond with: **N** to disable, or **Y** to enable:"))
                while True:
                    wildconfigset = await Meowth.wait_for_message(author=owner, check=lambda message: message.server is None)
                    if wildconfigset.content.lower() == "y":
                        server_dict_temp['wildset']=True
                        await Meowth.send_message(owner, _("**Wild Reports enabled**\n---"))
                        break
                    elif wildconfigset.content.lower() == "n":
                        server_dict_temp['wildset']=False
                        await Meowth.send_message(owner, _("**Wild Reports disabled**\n---"))
                        break
                    elif wildconfigset.content.lower() == "cancel":
                        await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
                        return
                    else:
                        await Meowth.send_message(owner, _("I'm sorry I don't understand. Please reply with either **N** to disable, or **Y** to enable."))
                        continue
                break
            await Meowth.send_message(owner, _("""**Pokemon Notifications**\nThe !want and !unwant commands let you add or remove roles for Pokemon that will be mentioned in reports. This let you get notifications on the Pokemon you want to track. I just need to know what channels you want to allow people to manage their pokemon with the !want and !unwant command. If you pick a channel that doesn't exist, I'll make it for you.\nIf you don't want to allow the management of tracked Pokemon roles, then you may want to disable this feature.\n\nRepond with: **N** to disable, or the **channel-name** list to enable, each seperated by a comma and space."""))
            while True:
                wantchs = await Meowth.wait_for_message(author=owner, check=lambda message: message.server is None)
                if wantchs.content.lower() == "n":
                    server_dict_temp['wantset']=False
                    await Meowth.send_message(owner, _("**Pokemon Notifications disabled**\n---"))
                    break
                else:
                    want_list = wantchs.content.lower().split(', ')
                    server_channel_list = []
                    for channel in server.channels:
                        server_channel_list.append(channel.name)
                    diff = set(citychannel_list) - set(server_channel_list)
                    if not diff:
                        server_dict_temp['wantset']=True
                        await Meowth.send_message(owner, _("**Pokemon Notifications enabled**\n---"))
                        while True:
                            try:
                                for want_channel_name in want_list:
                                    want_channel = discord.utils.get(server.channels, name = want_channel_name)
                                    if want_channel == None:
                                        want_channel = await Meowth.create_channel(server, want_channel_name)
                                    if want_channel not in server_dict_temp['want_channel_list']:
                                        server_dict_temp['want_channel_list'].append(want_channel)
                                break
                            except:
                                await Meowth.send_message(owner, _("Meowth! You didn't give me enough permissions to create channels! Please check my permissions and that my role is above general roles. Let me know if you'd like me to check again.\n\nRespond with: **Y** to try again, or **N** to skip and create the missing channels yourself."))
                                while True:
                                    wantpermswait = await Meowth.wait_for_message(author=owner, check=lambda message: message.server is None)
                                    if wantpermswait.content.lower() == "n":
                                        break
                                    elif wantpermswait.content.lower() == "y":
                                        break
                                    elif wantpermswait.content.lower() == "cancel":
                                        await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
                                        return
                                    else:
                                        await Meowth.send_message(owner, _("I'm sorry I don't understand. Please reply with either **Y** to try again, or **N** to skip and create the missing channels yourself."))
                                        continue
                                if wantpermswait.content.lower() == "y":
                                    continue
                                break
                    else:
                        await Meowth.send_message(owner, _("The channel list you provided doesn't match with your servers channels.\nThe following aren't in your server:{invalid_channels}\nPlease double check your channel list and resend your reponse.").format(invalid_channels=", ".join(diff)))
                        continue
                    break
            if server_dict_temp['raidset'] == True:
                await Meowth.send_message(owner, _("**Timezone Configuration**\nTo help coordinate raids reports for you, I need to know what timezone you're in! The current 24-hr time UTC is {utctime}. How many hours off from that are you?\n\nRespond with: A number from **-12** to **12**:").format(utctime=strftime("%H:%M",time.gmtime())))
                while True:
                    offsetmsg = await Meowth.wait_for_message(author = owner, check=lambda message: message.server is None)
                    if offsetmsg.content.lower() == "cancel":
                        await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
                        return
                    else:
                        try:
                            offset = float(offsetmsg.content)
                        except ValueError:
                            await Meowth.send_message(owner, _("I couldn't convert your answer to an appropriate timezone!.\nPlease double check what you sent me and resend a number strarting from **-12** to **12**."))
                            continue
                        if not -12 <= offset <= 14:
                            await Meowth.send_message(owner, _("I couldn't convert your answer to an appropriate timezone!.\nPlease double check what you sent me and resend a number strarting from **-12** to **12**."))
                            continue
                        else:
                            break
                server_dict_temp['offset'] = offset
                await Meowth.send_message(owner, _("**Timezone set**\n---"))
                break
            else:
                break
        elif otherreply.content.lower() == "n":
            server_dict_temp['other']=False
            server_dict_temp['raidset']=False
            server_dict_temp['wildset']=False
            server_dict_temp['wantset']=False
            server_dict_temp['done']=True
            await Meowth.send_message(owner, _("**Main Functions disabled**\n---"))
            break
        elif otherreply.content.lower == "cancel":
            await Meowth.send_message(owner, _("**CONFIG CANCELLED!**\nNo changes have been made."))
            return
        else:
            await Meowth.send_message(owner, _("I'm sorry I don't understand. Please reply with either **N** to disable, or **Y** to enable."))
            continue
            
    server_dict_temp['done']=True
    server_dict[server] = server_dict_temp
    await Meowth.send_message(owner, _("Meowth! Alright! Your settings have been saved and I'm ready to go! If you need to change any of these settings, just type !configure in your server again."))
    
"""Welcome message to the server and some basic instructions."""

@Meowth.event
async def on_member_join(member):
    server = member.server
    if server_dict[server]['done'] == False or server_dict[server]['welcome'] == False:
        return
    # Build welcome message

    admin_message = _(" If you have any questions just ask an admin.")

    welcomemessage = _("Meowth! Welcome to {server_name}, {new_member_name}! ")
    if server_dict[server]['team'] == True:
        welcomemessage += _("Set your team by typing {team_command} without quotations.").format(team_command=team_msg)
    welcomemessage += admin_message
    if server_dict[server]['zone'] == True:
        welcomemessage += _("Declare which regions you want to opt-in or opt-out by typing {join_command} or {leave_command} without quotations.").format(join_command=join_msg)
    if server_dict[server]['welcomechan'] == "dm":
        await Meowth.send_message(member, welcomemessage.format(server_name=server.name, new_member_name=member.mention))        
        
    else:
        default = discord.utils.get(server.channels, name = server_dict[server]['welcomechan'])
        if not default:
            print(_("WARNING: no default channel configured. Unable to send welcome message."))
        await Meowth.send_message(default, welcomemessage.format(server_name=server.name, new_member_name=member.mention))


"""

Admin commands

"""

async def _save():
    with tempfile.NamedTemporaryFile(
        'wb', dir=os.path.dirname('serverdict'), delete=False) as tf:
        pickle.dump(server_dict, tf)
        tempname = tf.name
    try:
        os.remove('serverdict_backup')
    except OSError as e:
        pass
    try:
        os.rename('serverdict', 'serverdict_backup')
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise
    
    os.rename(tempname, 'serverdict')
    
    # fd = open("serverdict", "wb")
    # pickle.dump(server_dict, fd)
    # fd.close()

@Meowth.command(pass_context=True, hidden=True)
async def exit(ctx):
    """Exit after saving.

    Usage: !exit.
    Calls the save function and quits the script."""
    member = ctx.message.author
    if check_master(member):
        try:
            await _save()
        except Exception as err:
            print(_("Error occured while trying to save!"))
            print(err)
        quit()
    else:
        raise_admin_violation(ctx.message)
    
@Meowth.command(pass_context=True, hidden=True)
async def save(ctx):
    """Save persistent state to file.

    Usage: !save
    File path is relative to current directory."""
    member = ctx.message.author
    if check_master(member):
        space1 = ctx.message.content.find(" ")
        if space1 == -1:
            try:
                await _save()
                logger.info("CONFIG SAVED")
            except Exception as err:
                print(_("Error occured while trying to save!"))
                print(err)
    else:
        raise_admin_violation(ctx.message)


"""

End admin commands

"""


@Meowth.command(pass_context = True)
async def team(ctx):
    """Set your team role.

    Usage: !team <team name>
    The team roles have to be created manually beforehand by the server administrator."""

    server = ctx.message.server
    toprole = server.me.top_role.name
    position = server.me.top_role.position
    high_roles = []

    for team in config['team_dict'].keys():
        temp_role = discord.utils.get(ctx.message.server.roles, name=team)
        if temp_role.position > position:
            high_roles.append(temp_role.name)

    if high_roles:
        await Meowth.send_message(ctx.message.channel, _("Meowth! My roles are ranked lower than the following team roles: **{higher_roles_list}**\nPlease get an admin to move my roles above them!").format(higher_roles_list=', '.join(high_roles)))
        return

    role = None
    entered_team = ctx.message.content[6:].lower()
    role = discord.utils.get(ctx.message.server.roles, name=entered_team)

    # Check if user already belongs to a team role by
    # getting the role objects of all teams in team_dict and
    # checking if the message author has any of them.
    for team in config['team_dict'].keys():
        temp_role = discord.utils.get(ctx.message.server.roles, name=team)
        # If the role is valid,
        if temp_role:
            # and the user has this role,
            if temp_role in ctx.message.author.roles:
                # then report that a role is already assigned
                await Meowth.send_message(ctx.message.channel, _("Meowth! You already have a team role!"))
                return
        # If the role isn't valid, something is misconfigured, so fire a warning.
        else:
            print(_("WARNING: Role {team_role} in team_dict not configured as a role on the server!").format(team_role=team))
    # Check if team is one of the three defined in the team_dict

    if entered_team not in list(config['team_dict'].keys()):
        await Meowth.send_message(ctx.message.channel, _("Meowth! \"{entered_team}\" isn't a valid team! Try {available_teams}").format(entered_team=entered_team, available_teams=team_msg))
        return
    # Check if the role is configured on the server
    elif role is None:
        await Meowth.send_message(ctx.message.channel, _("Meowth! The \"{entered_team}\" role isn't configured on this server! Contact an admin!").format(entered_team=entered_team))
    else:
        try:
            await Meowth.add_roles(ctx.message.author, role)
            await Meowth.send_message(ctx.message.channel, _("Meowth! Added {member} to Team {team_name}! {team_emoji}").format(member=ctx.message.author.mention, team_name=role.name.capitalize(), team_emoji=config['team_dict'][entered_team]))
        except discord.Forbidden:
            await Meowth.send_message(ctx.message.channel, _("Meowth! I can't add roles!"))

@Meowth.command(pass_context = True)
async def want(ctx):
    """A command for declaring a Pokemon species the user wants.

    Usage: !want <species>
    Meowth will mention you if anyone reports seeing
    this species in their !wild or !raid command.
    This command only works in #meowth-chat."""

    """Behind the scenes, Meowth tracks user !wants by
    creating a server role for the Pokemon species, and
    assigning it to the user."""

    if server_dict[ctx.message.server]['wantset'] == True:
        if server_dict[ctx.message.server]['want_channel_list'] and ctx.message.channel not in server_dict[ctx.message.server]['want_channel_list']:
            await Meowth.send_message(ctx.message.channel, _("Meowth! Please use one of the following channels for **!want** commands: {want_channel_list}").format(want_channel_list=", ".join(i.mention for i in server_dict[ctx.message.server]['want_channel_list'])))
            return
        else:
            entered_want = ctx.message.content[6:].lower()
            if entered_want not in pkmn_info['pokemon_list']:
                await Meowth.send_message(ctx.message.channel, spellcheck(entered_want))
                return
            role = discord.utils.get(ctx.message.server.roles, name=entered_want)
            # Create role if it doesn't exist yet
            if role is None:
                role = await Meowth.create_role(server = ctx.message.server, name = entered_want, hoist = False, mentionable = True)
                await asyncio.sleep(0.5)

            # If user is already wanting the Pokemon,
            # print a less noisy message
            if role in ctx.message.author.roles:
                await Meowth.send_message(ctx.message.channel, content=_("Meowth! {member}, I already know you want {pokemon}!").format(member=ctx.message.author.mention, pokemon=entered_want.capitalize()))
            else:
                await Meowth.add_roles(ctx.message.author, role)
                want_number = pkmn_info['pokemon_list'].index(entered_want) + 1
                want_img_url = "http://floatzel.net/pokemon/black-white/sprites/images/{0}.png".format(str(want_number)) #This part embeds the sprite
                want_embed = discord.Embed(colour=discord.Colour(0x2ecc71))
                want_embed.set_thumbnail(url=want_img_url)
                await Meowth.send_message(ctx.message.channel, content=_("Meowth! Got it! {member} wants {pokemon}").format(member=ctx.message.author.mention, pokemon=entered_want.capitalize()),embed=want_embed)

@Meowth.command(pass_context = True)
async def wild(ctx):
    """Report a wild Pokemon spawn location.

    Usage: !wild <species> <location>
    Meowth will insert the details (really just everything after the species name) into a
    Google maps link and post the link to the same channel the report was made in."""

    await _wild(ctx.message)

async def _wild(message):
    if server_dict[message.server]['wildset'] == True:
        if message.channel.name not in server_dict[message.server]['city_channels'].keys() and message.channel != message.server.default_channel:
            await Meowth.send_message(message.channel, _("Meowth! Please restrict wild reports to city channels or the default channel!"))
            return
        space1 = message.content.find(" ",6)
        if space1 == -1:
            await Meowth.send_message(message.channel, _("Meowth! Give more details when reporting! Usage: **!wild <pokemon name> <location>**"))
            return
        else:
            content = message.content[6:].lower()
            entered_wild = content.split(' ',1)[0]
            wild_details = content.split(' ',1)[1]
            if entered_wild not in pkmn_info['pokemon_list']:
                entered_wild2 = ' '.join([content.split(' ',2)[0],content.split(' ',2)[1]])
                if entered_wild2 in pkmn_info['pokemon_list']:
                    entered_wild = entered_wild2
                    try:
                        wild_details = content.split(' ',2)[2]
                    except IndexError:
                        await Meowth.send_message(message.channel, _("Meowth! Give more details when reporting! Usage: **!wild <pokemon name> <location>**"))
                        return
            wild_gmaps_link = create_gmaps_query(wild_details, message.channel)


        if entered_wild not in pkmn_info['pokemon_list']:
            await Meowth.send_message(message.channel, spellcheck(entered_wild))
            return
        else:
            wild = discord.utils.get(message.server.roles, name = entered_wild)
            if wild is None:
                wild = await Meowth.create_role(server = message.server, name = entered_wild, hoist = False, mentionable = True)
                await asyncio.sleep(0.5)
            wild_number = pkmn_info['pokemon_list'].index(entered_wild) + 1
            wild_img_url = "http://floatzel.net/pokemon/black-white/sprites/images/{0}.png".format(str(wild_number))
            wild_embed = discord.Embed(title=_("Meowth! Click here for directions to the wild {pokemon}!").format(pokemon=entered_wild.capitalize()),url=wild_gmaps_link,description=_("This is just my best guess!"),colour=discord.Colour(0x2ecc71))
            wild_embed.set_thumbnail(url=wild_img_url)
            await Meowth.send_message(message.channel, content=_("Meowth! Wild {pokemon} reported by {member}! Details: {location_details}").format(pokemon=wild.mention, member=message.author.mention, location_details=wild_details),embed=wild_embed)
    else:
        await Meowth.send_message(message.channel, _("Meowth! **!wild** commands have been disabled."))


@Meowth.command(pass_context=True)
async def raid(ctx):
    """Report an ongoing raid.

    Usage: !raid <species> <location>
    Meowth will insert the details (really just everything after the species name) into a
    Google maps link and post the link to the same channel the report was made in.
    Meowth's message will also include the type weaknesses of the boss.

    Finally, Meowth will create a separate channel for the raid report, for the purposes of organizing the raid."""

    await _raid(ctx.message)

async def _raid(message):
    if server_dict[message.server]['raidset'] == True:
        if message.channel.name not in server_dict[message.server]['city_channels'].keys():
            await Meowth.send_message(message.channel, _("Meowth! Please restrict raid reports to a city channel!"))
            return
        space1 = message.content.find(" ",6)
        if space1 == -1:
            await Meowth.send_message(message.channel, _("Meowth! Give more details when reporting! Usage: **!raid <pokemon name> <location>**"))
            return
        entered_raid = message.content[6:space1].lower()
        raid_message = message.content[space1:]
        raidtime = re.search('[01]:[0-5][0-9]', message.content)
        if raidtime:
            raid_details = message.content[space1:raidtime.start()-1]
            raidexp = raidtime.group()
        else:
            raid_details = raid_message
        raid_gmaps_link = create_gmaps_query(raid_details, message.channel)

        if entered_raid not in pkmn_info['pokemon_list']:
            await Meowth.send_message(message.channel, spellcheck(entered_raid))
            return
        if entered_raid not in pkmn_info['raid_list'] and entered_raid in pkmn_info['pokemon_list']:
            await Meowth.send_message(message.channel, _("Meowth! The Pokemon {pokemon} does not appear in raids!").format(pokemon=entered_raid.capitalize()))
            return
        else:
            raid_channel_name = entered_raid + sanitize_channel_name(raid_details)
            raid_channel = await Meowth.create_channel(message.server, raid_channel_name, *message.channel.overwrites)
            raid = discord.utils.get(message.server.roles, name = entered_raid)
            if raid is None:
                raid = await Meowth.create_role(server = message.server, name = entered_raid, hoist = False, mentionable = True)
                await asyncio.sleep(0.5)
            raid_number = pkmn_info['pokemon_list'].index(entered_raid) + 1
            raid_img_url = "http://floatzel.net/pokemon/black-white/sprites/images/{0}.png".format(str(raid_number))
            raid_embed = discord.Embed(title=_("Meowth! Click here for directions to the raid!"),url=raid_gmaps_link,description=_("Weaknesses: {weakness_list}").format(weakness_list=weakness_to_str(message.server, get_weaknesses(entered_raid))),colour=discord.Colour(0x2ecc71))
            raid_embed.set_thumbnail(url=raid_img_url)
            raidreport = await Meowth.send_message(message.channel, content = _("Meowth! {pokemon} raid reported by {member}! Details: {location_details}. Coordinate in {raid_channel}").format(pokemon=raid.mention, member=message.author.mention, location_details=raid_details, raid_channel=raid_channel.mention),embed=raid_embed)
            await asyncio.sleep(1) #Wait for the channel to be created.

            raidmsg = _("""Meowth! {pokemon} raid reported by {member}! Details: {location_details}. Coordinate here!

To update your status, choose from the following commands:
**!maybe, !coming, !here, !cancel**
If you are bringing more than one trainer/account, add the number of accounts total on your first status update.
Example: `!coming 5`

To see the list of trainers who have given their status:
**!interest, !otw, !waiting**

**!location** will show the current raid location.
**!location new <address>** will let you correct the raid address.
Sending a Google Maps link will also update the raid location.

**!timer** will show the current raid time.
**!timerset** will let you correct the raid countdown time.

Message **!starting** when the raid is beginning to clear the raid's 'here' list.

This channel will be deleted in 2 hours or five minutes after the timer expires.""").format(pokemon=raid.mention, member=message.author.mention, location_details=raid_details)
            raidmessage = await Meowth.send_message(raid_channel, content = raidmsg, embed=raid_embed)

            server_dict[message.server]['raidchannel_dict'][raid_channel] = {
                'reportcity' : message.channel.name,
                'trainer_dict' : {},
                'exp' : time.time() + 2 * 60 * 60, # Two hours from now
                'manual_timer' : False, # No one has explicitly set the timer, Meowth is just assuming 2 hours
                'active' : True,
                'raidmessage' : raidmessage,
                'raidreport' : raidreport,
                'address' : raid_details
                }
            
            if raidtime:
                await _timerset(raid_channel,raidexp)
            else:
                await Meowth.send_message(raid_channel, content = _("Meowth! Hey {member}, if you can, set the time left on the raid using **!timerset H:MM** so others can check it with **!timer**.").format(member=message.author.mention))
            
            event_loop.create_task(expiry_check(raid_channel))
    else:
        await Meowth.send_message(message.channel, _("Meowth! **!raid** commands have been disabled."))

@Meowth.command(pass_context=True)
async def unwant(ctx):
    """A command for removing the a !want for a Pokemon.

    Usage: !unwant <species>
    You will no longer be notified of reports about this Pokemon."""

    """Behind the scenes, Meowth removes the user from
    the server role for the Pokemon species."""

    if server_dict[ctx.message.server]['wantset'] == True:
        entered_unwant = ctx.message.content[8:].lower()
        role = discord.utils.get(ctx.message.server.roles, name=entered_unwant)
        if entered_unwant not in pkmn_info['pokemon_list']:
            await Meowth.send_message(ctx.message.channel, spellcheck(entered_unwant))
            return
        else:
            # Create role if it doesn't exist yet
            if role is None:
                role = await Meowth.create_role(server = ctx.message.server, name = entered_unwant, hoist = False, mentionable = True)
                await asyncio.sleep(0.5)

            # If user is not already wanting the Pokemon,
            # print a less noisy message
            if role not in ctx.message.author.roles:
                await Meowth.add_reaction(ctx.message, '✅')
                #await Meowth.send_message(ctx.message.channel, content=_("Meowth! {member}, I already know you don't want {pokemon}!").format(member=ctx.message.author.mention, pokemon=entered_unwant.capitalize()))
            else:
                await Meowth.remove_roles(ctx.message.author, role)
                unwant_number = pkmn_info['pokemon_list'].index(entered_unwant) + 1
                await Meowth.add_reaction(ctx.message, '✅')
                #unwant_img_url = "http://floatzel.net/pokemon/black-white/sprites/images/{0}.png".format(str(unwant_number))
                #unwant_embed = discord.Embed(colour=discord.Colour(0x2ecc71))
                #unwant_embed.set_thumbnail(url=unwant_img_url)
                #await Meowth.send_message(ctx.message.channel, content=_("Meowth! Got it! {member} no longer wants {pokemon}").format(member=ctx.message.author.mention, pokemon=entered_unwant.capitalize()),embed=unwant_embed)

@Meowth.command(pass_context = True)
async def join(ctx):
    """A command for declaring a region the user wants.
    Usage: !join <zone>
    Meowth will allow you access to the zone(s) you desire. #meowth-chat."""

    """Behind the scenes, Meowth assigns it to the user."""

    if server_dict[ctx.message.server]['zoneset'] == True:
        if server_dict[ctx.message.server]['want_channel_list'] and ctx.message.channel not in server_dict[ctx.message.server]['want_channel_list']:
            await Meowth.send_message(ctx.message.channel, _("Meowth! Please use one of the following channels for **!join** commands: {want_channel_list}").format(want_channel_list=", ".join(i.mention for i in server_dict[ctx.message.server]['want_channel_list'])))
            return
        else:
            entered_join = ctx.message.content[6:].lower()
            if entered_join not in zone_list['zone_list']:#I think something isn't right here
                await Meowth.send_message(ctx.message.channel, spellcheck(entered_join))
                return
            role = discord.utils.get(ctx.message.server.roles, name=entered_join)
            
            # If user is already wanting the Pokemon,
            # print a less noisy message
            if role in ctx.message.author.roles:
                await Meowth.send_message(ctx.message.channel, content=_("Meowth! {member}, I already know you're in {role}!").format(member=ctx.message.author.mention, role=entered_join.capitalize()))
            else:
                await Meowth.add_roles(ctx.message.author, role)
                await Meowth.send_message(ctx.message.channel, content=_("Meowth! Got it! {member} joined {role}").format(member=ctx.message.author.mention, role=entered_join.capitalize()))

@Meowth.command(pass_context=True)
async def leave(ctx):
    """A command for leaving a region.
    Usage: !leave <region>
    You will no longer have access to this role's priveleges."""

    """Behind the scenes, Meowth removes the user from
    the server role."""

    if server_dict[ctx.message.server]['zoneset'] == True:
        entered_leave = ctx.message.content[8:].lower()
        role = discord.utils.get(ctx.message.server.roles, name=entered_leave)
        if entered_leave not in zone_list['zone_list']:#I think this is also incorrect
            await Meowth.send_message(ctx.message.channel, spellcheck(entered_leave))
            return
        else:
            
            # If user is not already wanting the Pokemon,
            # print a less noisy message
            if role not in ctx.message.author.roles:
                await Meowth.add_reaction(ctx.message, '✅')
            else:
                await Meowth.remove_roles(ctx.message.author, role)
                unwant_number = zone_list['zone_list'].index(entered_leave) + 1#almost certain this is wrong
                await Meowth.add_reaction(ctx.message, '✅')

# Print raid timer
async def print_raid_timer(channel):
    localexpiresecs = server_dict[channel.server]['raidchannel_dict'][channel]['exp'] + 3600 * server_dict[channel.server]['offset']
    localexpire = time.gmtime(localexpiresecs)
    if not server_dict[channel.server]['raidchannel_dict'][channel]['active']:
        await Meowth.send_message(channel, _("Meowth! This raid's timer has already expired as of {expiry_time}!").format(expiry_time=strftime("%I:%M", localexpire)))
    else:
        if server_dict[channel.server]['raidchannel_dict'][channel]['manual_timer']:
            await Meowth.send_message(channel, _("Meowth! This raid will end at {expiry_time}!").format(expiry_time=strftime("%I:%M", localexpire)))
        else:
            await Meowth.send_message(channel, _("Meowth! No one told me when the raid ends, so I'm assuming it will end at {expiry_time}!").format(expiry_time=strftime("%I:%M", localexpire)))


async def _timerset(channel, exptime):


    # Meowth saves the timer message in the channel's 'exp' field.
    if channel in server_dict[channel.server]['raidchannel_dict']:
        ticks = time.time()
        try:
            h, m = exptime.split(':')
            s = int(h) * 3600 + int(m) * 60
            if s >= 7200:
                await Meowth.send_message(channel, _("Meowth...that's too long. Raids currently last no more than two hours..."))
                return
            if int(h) < 0 or int(m) < 0:
                await Meowth.send_message(channel, _("Meowth...I can't do that! No negative numbers, please!"))
                return
        except:
            await Meowth.send_message(channel, _("Meowth...I couldn't understand your time format..."))
            return
        expire = ticks + s


        # Update timestamp
        server_dict[channel.server]['raidchannel_dict'][channel]['exp'] = expire
        # Reactivate channel
        if not server_dict[channel.server]['raidchannel_dict'][channel]['active']:
            await Meowth.send_message(channel, "The channel has been reactivated.")
        server_dict[channel.server]['raidchannel_dict'][channel]['active'] = True
        # Mark that timer has been manually set
        server_dict[channel.server]['raidchannel_dict'][channel]['manual_timer'] = True
        # Send message
        await print_raid_timer(channel)
        # Trigger channel cleanup
        await expiry_check(channel)

@Meowth.command(pass_context=True)
async def timerset(ctx):
    """Set the remaining duration on a raid.

    Usage: !timerset <HH:MM>
    Works only in raid channels, can be set or overridden by anyone.
    Meowth displays the end time in HH:MM local time."""
    exptime = re.search('[01]:[0-5][0-9]', ctx.message.content)
    if exptime:
        await _timerset(ctx.message.channel, exptime.group(0))
    else:
        await Meowth.send_message(ctx.message.channel, _("Meowth... I couldn't understand your time format. Try again like this: !timerset H:MM"))

@Meowth.command(pass_context=True)
async def timer(ctx):
    """Have Meowth resend the expire time message for a raid.

    Usage: !timer
    The expiry time should have been previously set with !timerset."""
    await _timer(ctx)

async def _timer(ctx):
    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict']:
        await print_raid_timer(ctx.message.channel)


"""
Behind-the-scenes functions for raid management.
Triggerable through commands or through emoji
"""
async def _maybe(message, count):
    if message.channel in server_dict[message.server]['raidchannel_dict'] and server_dict[message.server]['raidchannel_dict'][message.channel]['active']:
        trainer_dict = server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict']
        if count == 1:
            await Meowth.send_message(message.channel, _("Meowth! {member} is interested!").format(member=message.author.mention))
        else:
            await Meowth.send_message(message.channel, _("Meowth! {member} is interested with a total of {trainer_count} trainers!").format(member=message.author.mention, trainer_count=count))
        # Add trainer name to trainer list
        if message.author.mention not in server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict']:
            trainer_dict[message.author.mention] = {}
        trainer_dict[message.author.mention]['status'] = "maybe"
        trainer_dict[message.author.mention]['count'] = count
        server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict'] = trainer_dict

async def _coming(message, count):
    if message.channel in server_dict[message.server]['raidchannel_dict'] and server_dict[message.server]['raidchannel_dict'][message.channel]['active']:
        trainer_dict = server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict']

        # TODO: handle case where a user sends !coming
        # after they've already sent !here
        if count == 1:
            await Meowth.send_message(message.channel, _("Meowth! {member} is on the way!").format(member=message.author.mention))
        else:
            await Meowth.send_message(message.channel, _("Meowth! {member} is on the way with a total of {trainer_count} trainers!").format(member=message.author.mention, trainer_count=count))
        # Add trainer name to trainer list
        if message.author.mention not in trainer_dict:
            trainer_dict[message.author.mention] = {}
        trainer_dict[message.author.mention]['status'] = "omw"
        trainer_dict[message.author.mention]['count'] = count
        server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict'] = trainer_dict


async def _here(message, count):
    if message.channel in server_dict[message.server]['raidchannel_dict'] and server_dict[message.server]['raidchannel_dict'][message.channel]['active']:
        trainer_dict = server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict']
        if count == 1:
            await Meowth.send_message(message.channel, _("Meowth! {member} is at the raid!").format(member=message.author.mention))
        else:
            await Meowth.send_message(message.channel, _("Meowth! {member} is at the raid with a total of {trainer_count} trainers!").format(member=message.author.mention, trainer_count=count))
        # Add trainer name to trainer list
        if message.author.mention not in trainer_dict:
            trainer_dict[message.author.mention] = {}
        trainer_dict[message.author.mention]['status'] = "waiting"
        trainer_dict[message.author.mention]['count'] = count
        server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict'] = trainer_dict

async def _cancel(message):
    if message.channel in server_dict[message.server]['raidchannel_dict'] and server_dict[message.server]['raidchannel_dict'][message.channel]['active']:
        trainer_dict = server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict']
        if message.author.mention in trainer_dict and trainer_dict[message.author.mention]['status'] == "maybe":
            if trainer_dict[message.author.mention]['count'] == 1:
                await Meowth.send_message(message.channel, _("Meowth! {member} is no longer interested!").format(member=message.author.mention))
            else:
                await Meowth.send_message(message.channel, _("Meowth! {member} and their total of {trainer_count} trainers are no longer interested!").format(member=message.author.mention, trainer_count=trainer_dict[message.author.mention]['count']))
        if message.author.mention in trainer_dict and trainer_dict[message.author.mention]['status'] == "waiting":
            if trainer_dict[message.author.mention]['count'] == 1:
                await Meowth.send_message(message.channel, _("Meowth! {member} has left the raid!").format(member=message.author.mention))
            else:
                await Meowth.send_message(message.channel, _("Meowth! {member} and their total of {trainer_count} trainers have left the raid!").format(member=message.author.mention, trainer_count=trainer_dict[message.author.mention]['count']))
        if message.author.mention in trainer_dict and trainer_dict[message.author.mention]['status'] == "omw":
            if trainer_dict[message.author.mention]['count'] == 1:
                await Meowth.send_message(message.channel, _("Meowth! {member} is no longer on their way!").format(member=message.author.mention))
            else:
                await Meowth.send_message(message.channel, _("Meowth! {member} and their total of {trainer_count} trainers are no longer on their way!").format(member=message.author.mention, trainer_count=trainer_dict[message.author.mention]['count']))
        del trainer_dict[message.author.mention]
        server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict'] = trainer_dict

@Meowth.command(pass_context=True)
async def maybe(ctx):
    """Indicate you are interested in the raid.

    Usage: !maybe [message]
    Works only in raid channels. If message is omitted, assumes you are a group of 1.
    Otherwise, this command expects at least one word in your message to be a number,
    and will assume you are a group with that many people."""
    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        trainer_dict = server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict']
        count = 1
        space1 = ctx.message.content.find(" ")
        if space1 != -1:
            # Search for a number in the message
            # by trying to convert each word to integer
            count = None
            duplicate = False
            for word in ctx.message.content[7:].split():
                try:
                    newcount = int(word)
                    if not count:
                        count = newcount
                    else:
                        duplicate = True
                except ValueError:
                    pass
            # If count wasn't set, we didn't find a number
            if not count:
                await Meowth.send_message(ctx.message.channel, _("Meowth! Exactly *how many* are interested? There wasn't a number anywhere in your message. Or, just say `!maybe` if you're by yourself."))
                return
            # Don't allow duplicates
            if duplicate:
                await Meowth.send_message(ctx.message.channel, _("Meowth...I got confused because there were several numbers in your message. I don't know which one is the right one."))
                return
        await _maybe(ctx.message, count)


@Meowth.command(pass_context=True)
async def coming(ctx):
    """Indicate you are on the way to a raid.

    Usage: !coming [message]
    Works only in raid channels. If message is omitted, checks for previous !maybe
    command and takes the count from that. If it finds none, assumes you are a group
    of 1.
    Otherwise, this command expects at least one word in your message to be a number,
    and will assume you are a group with that many people."""
    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        trainer_dict = server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict']
        count = 1
        space1 = ctx.message.content.find(" ")
        if space1 == -1:
            # If there was a previous !maybe command, take the count from that
            if ctx.message.author.mention in trainer_dict:
                count = trainer_dict[ctx.message.author.mention]['count']
            else:
                count = 1
        if space1 != -1:
            # Search for a number in the message
            # by trying to convert each word to integer
            count = None
            duplicate = False
            for word in ctx.message.content[8:].split():
                try:
                    newcount = int(word)
                    if not count:
                        count = newcount
                    else:
                        duplicate = True
                except ValueError:
                    pass
            # If count wasn't set, we didn't find a number
            if not count:
                await Meowth.send_message(ctx.message.channel, _("Meowth! Exactly *how many* are coming? There wasn't a number anywhere in your message. Or, just say **!coming** if you're by yourself."))
                return
            # Don't allow duplicates
            if duplicate:
                await Meowth.send_message(ctx.message.channel, _("Meowth...I got confused because there were several numbers in your message. I don't know which one is the right one."))
                return

        await _coming(ctx.message, count)

@Meowth.command(pass_context=True)
async def here(ctx):
    """Indicate you have arrived at the raid.

    Usage: !here [message]
    Works only in raid channels. If message is omitted, and
    you have previously issued !coming, then preserves the count
    from that command. Otherwise, assumes you are a group of 1.
    Otherwise, this command expects at least one word in your message to be a number,
    and will assume you are a group with that many people."""
    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        trainer_dict = server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict']

        # If no message, default count is 1
        count = 1
        space1 = ctx.message.content.find(" ")
        if space1 == -1:
            # If there was a previous !coming command, take the count from that
            if ctx.message.author.mention in trainer_dict:
                count = trainer_dict[ctx.message.author.mention]['count']
            else:
                count = 1
        else:
            # Search for a number in the message
            # by trying to convert each word to integer
            count = None
            duplicate = False
            for word in ctx.message.content[6:].split():
                try:
                    newcount = int(word)
                    if not count:
                        count = newcount
                    else:
                        duplicate = True
                except ValueError:
                    pass
            # If count wasn't set, we didn't find a number
            if not count:
                await Meowth.send_message(ctx.message.channel, _("Meowth! Exactly *how many* are here? There wasn't a number anywhere in your message. Or, just say **!here** if you're by yourself."))
                return
            # Don't allow duplicates
            if duplicate:
                await Meowth.send_message(ctx.message.channel, _("Meowth...I got confused because there were several numbers in your message. I don't know which one is the right one."))
                return
        await _here(ctx.message, count)

@Meowth.command(pass_context=True)
async def cancel(ctx):
    """Indicate you are no longer interested in a raid.

    Usage: !cancel
    Works only in raid channels. Removes you and your party
    from the list of trainers who are "otw" or "here"."""
    await _cancel(ctx.message)

@Meowth.command(pass_context=True)
async def interest(ctx):
    """Lists the number and users who are interested in the raid.

    Usage: !interest
    Works only in raid channels."""
    await _interest(ctx)

async def _interest(ctx):

    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        ctx_maybecount = 0

        # Grab all trainers who are maybe and sum
        # up their counts
        trainer_dict = server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict']
        for trainer in trainer_dict.values():
            if trainer['status'] == "maybe":
                ctx_maybecount += trainer['count']

        # If at least 1 person is interested,
        # add an extra message indicating who it is.
        maybe_exstr = ""
        maybe_list = []
        for trainer in trainer_dict.keys():
            if trainer_dict[trainer]['status']=='maybe':
                maybe_list.append(trainer)
        if ctx_maybecount > 0:
            maybe_exstr = _(" including {trainer_list} and the people with them! Let them know if there is a group forming").format(trainer_list=", ".join(maybe_list))
        await Meowth.send_message(ctx.message.channel, _("Meowth! {trainer_count} interested{including_string}!").format(trainer_count=str(ctx_maybecount), including_string=maybe_exstr))


@Meowth.command(pass_context=True)
async def otw(ctx):
    """Lists the number and users who are on the way to a raid.

    Usage: !otw
    Works only in raid channels."""
    await _otw(ctx)

async def _otw(ctx):

    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        ctx_omwcount = 0

        # Grab all trainers who are :omw: and sum
        # up their counts
        trainer_dict = server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict']
        for trainer in trainer_dict.values():
            if trainer['status'] == "omw":
                ctx_omwcount += trainer['count']

        # If at least 1 person is on the way,
        # add an extra message indicating who it is.
        otw_exstr = ""
        otw_list = []
        for trainer in trainer_dict.keys():
            if trainer_dict[trainer]['status']=='omw':
                otw_list.append(trainer)
        if ctx_omwcount > 0:
            otw_exstr = _(" including {trainer_list} and the people with them! Be considerate and wait for them if possible").format(trainer_list=", ".join(otw_list))
        await Meowth.send_message(ctx.message.channel, _("Meowth! {trainer_count} on the way{including_string}!").format(trainer_count=str(ctx_omwcount), including_string=otw_exstr))

@Meowth.command(pass_context=True)
async def waiting(ctx):
    """List the number and users who are waiting at a raid.

    Usage: !waiting
    Works only in raid channels."""
    await _waiting(ctx)

async def _waiting(ctx):

    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        ctx_waitingcount = 0

        # Grab all trainers who are :here: and sum
        # up their counts
        trainer_dict = server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict']
        for trainer in trainer_dict.values():
            if trainer['status'] == "waiting":
                ctx_waitingcount += trainer['count']

        # If at least 1 person is waiting,
        # add an extra message indicating who it is.
        waiting_exstr = ""
        waiting_list = []
        for trainer in trainer_dict.keys():
            if trainer_dict[trainer]['status']=='waiting':
                waiting_list.append(trainer)
        if ctx_waitingcount > 0:
            waiting_exstr = _(" including {trainer_list} and the people with them! Be considerate and let them know if and when you'll be there").format(trainer_list=", ".join(waiting_list))
        await Meowth.send_message(ctx.message.channel, _("Meowth! {trainer_count} waiting at the raid{including_string}!").format(trainer_count=str(ctx_waitingcount), including_string=waiting_exstr))

@Meowth.command(pass_context=True)
async def lists(ctx):
    """Print all lists concerning a raid at once in raid channels and lists all active raids in city channels.

    Usage: !lists
    Works only in raid or city channels. Calls the interest, otw, and waiting lists. Also prints
    the raid timer. In city channels, lists all active raids."""
    activeraidnum = 0
    if server_dict[ctx.message.server]['raidset'] == True:
        if ctx.message.channel.name in server_dict[ctx.message.server]['city_channels'].keys():
            await Meowth.send_message(ctx.message.channel, _("Current Raids in {0}:").format(ctx.message.channel.name.capitalize()))
            for activeraid in server_dict[ctx.message.server]['raidchannel_dict']:
                ctx_waitingcount = 0
                ctx_omwcount = 0
                ctx_maybecount = 0
                for trainer in server_dict[ctx.message.server]['raidchannel_dict'][activeraid]['trainer_dict'].values():
                    if trainer['status'] == "waiting":
                        ctx_waitingcount += trainer['count']
                    elif trainer['status'] == "omw":
                        ctx_omwcount += trainer['count']
                    elif trainer['status'] == "maybe":
                        ctx_maybecount += trainer['count']
                localexpire = time.gmtime(server_dict[ctx.message.channel.server]['raidchannel_dict'][activeraid]['exp'] + 3600 * server_dict[ctx.message.channel.server]['offset'])
                if server_dict[ctx.message.server]['raidchannel_dict'][activeraid]['reportcity'] == ctx.message.channel.name and server_dict[ctx.message.server]['raidchannel_dict'][activeraid]['active'] and discord.utils.get(ctx.message.channel.server.channels, id=activeraid.id):
                    await Meowth.send_message(ctx.message.channel, _("{0.mention} - interested = {1}, {2} = {3}, {4} = {5}, Ends at {6}").format(activeraid, ctx_maybecount, parse_emoji(ctx.message.server, config['omw_id']), ctx_omwcount, parse_emoji(ctx.message.server, config['here_id']), ctx_waitingcount, strftime("%I:%M", localexpire)))
                    activeraidnum += 1
            if activeraidnum == 0:
                await Meowth.send_message(ctx.message.channel, _("Meowth! No active raids! Report one with **!raid <name> <location>**."))
        elif ctx.message.channel in server_dict[ctx.message.channel.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
            await _interest(ctx)
            await _otw(ctx)
            await _waiting(ctx)
            await _timer(ctx)

@Meowth.command(pass_context=True)
async def starting(ctx):
    """Signal that a raid is starting.

    Usage: !starting
    Works only in raid channels. Sends a message and clears the waiting list. Users who are waiting
    for a second group must reannounce with the :here: emoji or !here."""

    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        ctx_startinglist = []

        trainer_dict = server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict']

        # Add all waiting trainers to the starting list
        for trainer in trainer_dict:
            if trainer_dict[trainer]['status'] == "waiting":
                ctx_startinglist.append(trainer)

        # Go back and delete the trainers from the waiting list
        for trainer in ctx_startinglist:
            del trainer_dict[trainer]
        server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict'] = trainer_dict


        starting_str = _("Meowth! The group that was waiting is starting the raid! Trainers {trainer_list}, please respond with {here_emoji} or !here if you are waiting for another group!").format(trainer_list=", ".join(ctx_startinglist), here_emoji=parse_emoji(ctx.message.server, config['here_id']))
        if len(ctx_startinglist) == 0:
            starting_str = _("Meowth! How can you start when there's no one waiting at this raid!?")
        await Meowth.send_message(ctx.message.channel, starting_str)


@Meowth.command(pass_context=True, hidden=True)
async def omw(ctx):
    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        await Meowth.send_message(ctx.message.channel, content = _("Meowth! Hey {member}, I don't know if you meant **!coming** to say that you are coming or **!otw** to see the other trainers on their way").format(member=ctx.message.author.mention))
        
@Meowth.event
async def on_message(message):
    if message.server is not None:
        raid_status = server_dict[message.server]['raidchannel_dict'].get(message.channel,None)
        if raid_status is not None:
            if server_dict[message.server]['raidchannel_dict'][message.channel]['active']:
                trainer_dict = server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict']
                if message.author.mention in trainer_dict:
                    count = trainer_dict[message.author.mention]['count']
                else:
                    count = 1
                omw_emoji = parse_emoji(message.server, config['omw_id'])
                if message.content.startswith(omw_emoji):
                    emoji_count = message.content.count(omw_emoji)
                    await _coming(message, emoji_count)
                    return
                here_emoji = parse_emoji(message.server, config['here_id'])
                if message.content.startswith(here_emoji):
                    emoji_count = message.content.count(here_emoji)
                    await _here(message, emoji_count)
                    return
        
    await Meowth.process_commands(message)
        
@Meowth.command(pass_context=True, hidden=True)
async def interested(ctx):
    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        await Meowth.send_message(ctx.message.channel, content = _("Meowth! Hey {member}, I don't know if you meant **!maybe** to say that you are interested or **!interest** to see the other trainers interest").format(member=ctx.message.author.mention))

@Meowth.command(pass_context=True)
async def duplicate(ctx):
    """A command to report a raid channel as a duplicate.

    Usage: !duplicate
    Works only in raid channels. When three users report a channel as a duplicate,
    Meowth deactivates the channel and marks it for deletion."""
    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        ctx_dupecount = 0
        trainer_dict = server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict']
        if ctx.message.author.mention not in server_dict[ctx.message.server]['raidchannel_dict']:
            trainer_dict[ctx.message.author.mention] = {}
        trainer_dict[ctx.message.author.mention]['dupe'] = "dupe"
        for trainer in trainer_dict.values():
            if trainer['dupe'] == "dupe":
                ctx_dupecount += 1
        if ctx_dupecount == 3:
            await Meowth.send_message(ctx.message.channel, _("This channel has been reported as a duplicate and has been deactivated. Check the channel list for the other raid channel to coordinate in! If this was an error you can reset the raid with **!timerset**"))
            server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active'] = False
            if discord.utils.get(ctx.message.channel.server.channels, id=ctx.message.channel.id):
                await asyncio.sleep(300)
                if server_dict[ctx.message.channel.server]['raidchannel_dict'][ctx.message.channel] and not server_dict[ctx.message.channel.server]['raidchannel_dict'][ctx.message.channel]['active']:
                    del server_dict[ctx.message.channel.server]['raidchannel_dict'][ctx.message.channel]
                    if discord.utils.get(ctx.message.channel.server.channels, id=ctx.message.channel.id):
                        await Meowth.delete_channel(ctx.message.channel)
                        return
            else:
                del server_dict[ctx.message.channel.server]['raidchannel_dict'][ctx.message.channel]
    server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['trainer_dict'] = trainer_dict
    
@Meowth.group(pass_context=True)
async def location(ctx):
    """Get raid location.

    Usage: !location
    Works only in raid channels. Gives the raid location link."""
    if ctx.invoked_subcommand is None:
        if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
            message = ctx.message
            raidmsg = server_dict[message.server]['raidchannel_dict'][message.channel]['raidmessage']
            location = server_dict[message.server]['raidchannel_dict'][message.channel]['address']
            report_city = server_dict[message.server]['raidchannel_dict'][message.channel]['reportcity']
            report_channel = discord.utils.get(message.server.channels, name=report_city)
            locurl = create_gmaps_query(location, report_channel)
            oldembed = raidmsg.embeds[0]
            newembed = discord.Embed(title=oldembed['title'],url=locurl,description=oldembed['description'],colour=discord.Colour(0x2ecc71))
            newembed.set_thumbnail(url=oldembed['thumbnail']['url'])
            await Meowth.send_message(message.channel, content = _("Meowth! Here's the current location for the raid!\nDetails:{location}").format(location = location), embed = newembed)


@location.command(pass_context=True)
async def new(ctx):
    """Change raid location.

    Usage: !location new <new address>
    Works only in raid channels. Changes the google map links."""
    
    if ctx.message.channel in server_dict[ctx.message.server]['raidchannel_dict'] and server_dict[ctx.message.server]['raidchannel_dict'][ctx.message.channel]['active']:
        message = ctx.message
        space1 = message.content.find(" ",13)
        if space1 == -1:
            await Meowth.send_message(message.channel, _("Meowth! We're missing the new location details! Usage: **!location new <new address>**"))
            return
        else:
            report_city = server_dict[message.server]['raidchannel_dict'][message.channel]['reportcity']
            report_channel = discord.utils.get(message.server.channels, name=report_city)
            
            details = message.content[space1:]
            if "/maps" in message.content:
                mapsindex = message.content.find("/maps")
                newlocindex = message.content.rfind("http", 0, mapsindex)
                if newlocindex == -1:
                    return
                newlocend = message.content.find(" ", newlocindex)
                if newlocend == -1:
                    newloc = message.content[newlocindex:]
                else:
                    newloc = message.content[newlocindex:newlocend+1]
            else:
                newloc = create_gmaps_query(details, report_channel)
                
            server_dict[message.server]['raidchannel_dict'][message.channel]['address'] = details
            oldraidmsg = server_dict[message.server]['raidchannel_dict'][message.channel]['raidmessage']
            oldreportmsg = server_dict[message.server]['raidchannel_dict'][message.channel]['raidreport']
            oldembed = oldraidmsg.embeds[0]
            newembed = discord.Embed(title=oldembed['title'],url=newloc,description=oldembed['description'],colour=discord.Colour(0x2ecc71))
            newembed.set_thumbnail(url=oldembed['thumbnail']['url'])
            await Meowth.edit_message(oldraidmsg, new_content=oldraidmsg.content, embed=newembed)
            await Meowth.edit_message(oldreportmsg, new_content=oldreportmsg.content, embed=newembed)
            otw_list = []
            trainer_dict = server_dict[message.server]['raidchannel_dict'][message.channel]['trainer_dict']
            for trainer in trainer_dict.keys():
                if trainer_dict[trainer]['status']=='omw':
                    otw_list.append(trainer)
            await Meowth.send_message(message.channel, content = _("Meowth! Someone has suggested a different location for the raid! Trainers {trainer_list}: make sure you are headed to the right place!").format(trainer_list=", ".join(otw_list)), embed = newembed)
            return








Meowth.run(config['bot_token'])
