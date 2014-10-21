#
#    Copyright (c) 2013-2014 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
#    $Id$
#
"""Publish weather data to RESTful sites such as the Weather Underground.

                            GENERAL ARCHITECTURE

Each protocol uses two classes:

 o A weewx service, that runs in the main thread. Call this the
    "controlling object"
 o A separate "threading" class that runs in its own thread. Call this the
    "posting object".
 
Communication between the two is via an instance of Queue.Queue. New loop
packets or archive records are put into the queue by the controlling object
and received by the posting object. Details below.
 
The controlling object should inherit from StdRESTful. The controlling object
is responsible for unpacking any configuration information from weewx.conf, and
supplying any defaults. It sets up the queue. It arranges for any new LOOP or
archive records to be put in the queue. It then launches the thread for the
posting object.
 
When a new LOOP or record arrives, the controlling object puts it in the queue,
to be received by the posting object. The controlling object can tell the
posting object to terminate by putting a 'None' in the queue.
 
The posting object should inherit from class weecore.restx.RESTThread. It monitors the queue
and blocks until a new record arrives.

The base class RESTThread has a lot of functionality, so specializing classes
should only have to implement a few functions. In particular, 

 - format_url(self, record). This function takes a record dictionary as an
   argument. It is responsible for formatting it as an appropriate URL. 
   For example, the station registry's version emits strings such as
     http://weewx.com/register/register.cgi?weewx_info=2.6.0a5&python_info= ...
   
 - skip_this_post(self, time_ts). If this function returns True, then the
   post will be skipped. Otherwise, it is done. The default version does two
   checks. First, it sees how old the record is. If it is older than the value
   'stale', then the post is skipped. Second, it will not allow posts more
   often than 'post_interval'. Both of these can be set in the constructor of
   RESTThread.
   
 - post_request(self, request). This function takes a urllib2.Request object
   and is responsible for performing the HTTP GET or POST. The default version
   simply uses urllib2.urlopen(request) and returns the result. If the post
   could raise an unusual exception, override this function and catch the
   exception. See the WOWThread implementation for an example.
   
 - check_response(). After an HTTP request gets posted, the webserver sends
   back a "response." This response may contain clues as to whether the post
   worked.  By overriding check_response() you can look for these clues. For
   example, the station registry checks all lines in the response, looking for
   any that start with the string "FAIL". If it finds one, it raises a
   FailedPost exception, signaling that the post did not work.
   
In unusual cases, you might also have to implement the following:
  
 - process_record(). The default version is for HTTP GET posts, but if you wish
   to do a POST or use a socket, you may need to provide a specialized version.
   See the CWOP version, CWOPThread.process_record(), for an example that
   uses sockets. 
"""
from __future__ import with_statement
import Queue
import datetime
import hashlib
import platform
import re
import socket
import sys
import syslog
import time
import urllib
import urllib2

import weedb
import weewx
import weecore.restx
import weeutil.weeutil
from weeutil.weeutil import to_float, to_bool, accumulateLeaves
import weecore.units


#==============================================================================
#                    Ambient protocols
#==============================================================================

class StdWunderground(weecore.restx.StdRESTful):
    """Specialized version of the Ambient protocol for the Weather Underground.
    """
    
    # The URLs used by the WU:
    rapidfire_url = "http://rtupdate.wunderground.com/weatherstation/updateweatherstation.php"
    archive_url   = "http://weatherstation.wunderground.com/weatherstation/updateweatherstation.php"

    def __init__(self, engine, config_dict):
        
        super(StdWunderground, self).__init__(engine, config_dict)
        
        # Extract the required parameters. If one of them is missing,
        # a KeyError exception will occur. Be prepared to catch it.
        try:
            # Extract a copy of the dictionary with the WU options:
            _ambient_dict = accumulateLeaves(config_dict['StdRESTful']['Wunderground'], max_level=1)
            # A convenient way to check for missing required key words.
            _ambient_dict['station']
            _ambient_dict['password']
        except KeyError, e:
            syslog.syslog(syslog.LOG_DEBUG, 
                          "restx: Wunderground: Data will not be posted: Missing option %s" % e)
            return

        # Get the database dictionary. Throw away the manager. We don't need it.
        _, _database_dict = weecore.archive.prep_database(config_dict, 'wx_binding')
        
        # The default is to not do an archive post if a rapidfire post
        # has been specified, but this can be overridden
        do_rapidfire_post = to_bool(_ambient_dict.pop('rapidfire', False))
        do_archive_post   = to_bool(_ambient_dict.pop('archive_post', not do_rapidfire_post))
        
        if do_archive_post:
            _ambient_dict.setdefault('server_url', StdWunderground.archive_url)
            self.archive_queue = Queue.Queue()
            self.archive_thread = AmbientThread(self.archive_queue,
                                                _database_dict,
                                                protocol_name="Wunderground-PWS",
                                                **_ambient_dict) 
            self.archive_thread.start()
            self.bind(weecore.NEW_ARCHIVE_RECORD, self.new_archive_record)
            syslog.syslog(syslog.LOG_INFO, "restx: Wunderground-PWS: "
                          "Data for station %s will be posted" % _ambient_dict['station'])

        if do_rapidfire_post:
            _ambient_dict.setdefault('server_url', StdWunderground.rapidfire_url)
            _ambient_dict.setdefault('log_success', False)
            _ambient_dict.setdefault('log_failure', False)
            _ambient_dict.setdefault('max_backlog', 0)
            _ambient_dict.setdefault('max_tries', 1)
            self.loop_queue = Queue.Queue()
            self.loop_thread = AmbientLoopThread(self.loop_queue,
                                                 _database_dict,
                                                 protocol_name="Wunderground-RF",
                                                 **_ambient_dict) 
            self.loop_thread.start()
            self.bind(weecore.NEW_LOOP_PACKET, self.new_loop_packet)
            syslog.syslog(syslog.LOG_INFO, 
                          "restx: Wunderground-RF: Data for station %s will be posted" %
                          _ambient_dict['station'])

    def new_loop_packet(self, event):
        """Puts new LOOP packets in the loop queue"""
        self.loop_queue.put(event.packet)

    def new_archive_record(self, event):
        """Puts new archive records in the archive queue"""
        self.archive_queue.put(event.record)
                
class StdPWSWeather(weecore.restx.StdRESTful):
    """Specialized version of the Ambient protocol for PWSWeather"""
    
    # The URL used by PWSWeather:
    archive_url = "http://www.pwsweather.com/pwsupdate/pwsupdate.php"

    def __init__(self, engine, config_dict):
        
        super(StdPWSWeather, self).__init__(engine, config_dict)
        
        # Extract the required parameters. If one of them is missing,
        # a KeyError exception will occur. Be prepared to catch it.
        try:
            # Extract a copy of the dictionary with the PWS options:
            _ambient_dict = accumulateLeaves(config_dict['StdRESTful']['PWSweather'], max_level=1)
            # A convenient way to check for missing required key words.
            _ambient_dict['station']
            _ambient_dict['password']
        except KeyError, e:
            syslog.syslog(syslog.LOG_DEBUG, 
                          "restx: PWSWeather: Data will not be posted: "
                          "Missing option %s" % e)
            return

        # Get the database dictionary. Throw away the manager. We don't need it.
        _, _database_dict = weecore.archive.prep_database(config_dict, 'wx_binding')
        
        _ambient_dict.setdefault('server_url', StdPWSWeather.archive_url)
        self.archive_queue = Queue.Queue()
        self.archive_thread = AmbientThread(self.archive_queue, _database_dict,
                                            protocol_name="PWSWeather",
                                            **_ambient_dict)
        self.archive_thread.start()
        self.bind(weecore.NEW_ARCHIVE_RECORD, self.new_archive_record)
        syslog.syslog(syslog.LOG_INFO, "restx: PWSWeather: Data for station %s will be posted" % 
                      _ambient_dict['station'])

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)

# For backwards compatibility with early alpha versions:
StdPWSweather = StdPWSWeather

class StdWOW(weecore.restx.StdRESTful):

    """Upload using the UK Met Office's WOW protocol. 
    
    For details of the WOW upload protocol, see 
    http://wow.metoffice.gov.uk/support/dataformats#dataFileUpload
    """

    # The URL used by WOW:
    archive_url = "http://wow.metoffice.gov.uk/automaticreading"

    def __init__(self, engine, config_dict):
        
        super(StdWOW, self).__init__(engine, config_dict)
        
        # Extract the required parameters. If one of them is missing,
        # a KeyError exception will occur. Be prepared to catch it.
        try:
            # Extract a copy of the dictionary with the WOW options:
            _ambient_dict = accumulateLeaves(config_dict['StdRESTful']['WOW'], max_level=1)
            # A convenient way to check for missing required key words.
            _ambient_dict['station']
            _ambient_dict['password']
        except KeyError, e:
            syslog.syslog(syslog.LOG_DEBUG, "restx: WOW: Data will not be posted: "
                          "Missing option %s" % e)
            return

        # Get the database dictionary. Throw away the manager. We don't need it.
        _, _database_dict = weecore.archive.prep_database(config_dict, 'wx_binding')
        
        _ambient_dict.setdefault('server_url', StdWOW.archive_url)
        self.archive_queue = Queue.Queue()
        self.archive_thread = WOWThread(self.archive_queue, _database_dict, 
                                        protocol_name="WOW",
                                        **_ambient_dict)
        self.archive_thread.start()
        self.bind(weecore.NEW_ARCHIVE_RECORD, self.new_archive_record)
        syslog.syslog(syslog.LOG_INFO, "restx: WOW: Data for station %s will be posted" % 
                      _ambient_dict['station'])
        
    def new_archive_record(self, event):
        self.archive_queue.put(event.record)

class AmbientThread(weecore.restx.RESTThread):
    """Concrete class for threads posting from the archive queue,
       using the Ambient PWS protocol."""
    
    def __init__(self, queue, database_dict,
                 station, password, server_url,
                 protocol_name="Unknown-Ambient",
                 post_interval=None, max_backlog=sys.maxint, stale=None, 
                 log_success=True, log_failure=True,
                 timeout=10, max_tries=3, retry_wait=5):
        """
        Initializer for the AmbientThread class.
        
        Required parameters:

          queue: An instance of Queue.Queue where the records will appear.
          
          database_dict: A dictionary holding the weedb database connection
          information. It will be used to open a connection to the archive 
          database.
          
          station: The name of the station. For example, for the WU, this
          would be something like "KORHOODR3".
          
          password: Password used for the station.
          
          server_url: An url where the server for this protocol can be found.

        Optional parameters:
        
          protocol_name: A string holding the name of the protocol.
          Default is "Unknown-Ambient"
          
          log_success: If True, log a successful post in the system log.
          Default is True.
          
          log_failure: If True, log an unsuccessful post in the system log.
          Default is True.
          
          max_backlog: How many records are allowed to accumulate in the queue
          before the queue is trimmed.
          Default is sys.maxint (essentially, allow any number).
          
          max_tries: How many times to try the post before giving up.
          Default is 3
          
          stale: How old a record can be and still considered useful.
          Default is None (never becomes too old).
          
          post_interval: How long to wait between posts.
          Default is None (post every record).
          
          timeout: How long to wait for the server to respond before giving up.
          Default is 10 seconds.        
        """
        super(AmbientThread, self).__init__(queue,
                                            protocol_name=protocol_name,
                                            database_dict=database_dict,
                                            post_interval=post_interval,
                                            max_backlog=max_backlog,
                                            stale=stale,
                                            log_success=log_success,
                                            log_failure=log_failure,
                                            timeout=timeout,
                                            max_tries=max_tries,
                                            retry_wait=retry_wait)
        self.station       = station
        self.password      = password
        self.server_url    = server_url

    # Types and formats of the data to be published:
    _formats = {'dateTime'    : 'dateutc=%s',
                'barometer'   : 'baromin=%.3f',
                'outTemp'     : 'tempf=%.1f',
                'outHumidity' : 'humidity=%03.0f',
                'windSpeed'   : 'windspeedmph=%03.0f',
                'windDir'     : 'winddir=%03.0f',
                'windGust'    : 'windgustmph=%03.0f',
                'dewpoint'    : 'dewptf=%.1f',
                'hourRain'    : 'rainin=%.2f',
                'dayRain'     : 'dailyrainin=%.2f',
                'radiation'   : 'solarradiation=%.2f',
                'UV'          : 'UV=%.2f',
                'realtime'    : 'realtime=%s',
                'rtfreq'      : 'rtfreq=%s'}
    
    def format_url(self, record):
        """Return an URL for posting using the Ambient protocol."""
        
        if weecore.debug:
            assert(record['usUnits'] == weecore.US)
    
        _liststr = ["action=updateraw", 
                    "ID=%s" % self.station,
                    "PASSWORD=%s" % self.password,
                    "softwaretype=weewx-%s" % weewx.__version__]
        
        # Go through each of the supported types, formatting it, then adding
        # to _liststr:
        for _key in AmbientThread._formats:
            _v = record.get(_key)
            # Check to make sure the type is not null
            if _v is not None :
                if _key == 'dateTime':
                    # For dates, convert from time stamp to a string, using
                    # what the Weather Underground calls "MySQL format." I've
                    # fiddled with formatting, and it seems that escaping the
                    # colons helps its reliability. But, I could be imagining
                    # things.
                    _v = urllib.quote(datetime.datetime.utcfromtimestamp(_v).isoformat('+'), '-+')
                # Format the value, and accumulate in _liststr:
                _liststr.append(AmbientThread._formats[_key] % _v)
        # Now stick all the pieces together with an ampersand between them:
        _urlquery = '&'.join(_liststr)
        # This will be the complete URL for the HTTP GET:
        _url = "%s?%s" % (self.server_url, _urlquery)
        return _url
                
    def check_response(self, response):
        """Check the HTTP response code for an Ambient related error."""
        for line in response:
            # PWSweather signals with 'ERROR', WU with 'INVALID':
            if line.startswith('ERROR') or line.startswith('INVALID'):
                # Bad login. No reason to retry. Raise an exception.
                raise weecore.restx.BadLogin(line)
        
class AmbientLoopThread(AmbientThread):
    """Version used for the Rapidfire protocol."""

    def get_record(self, record, archive):
        """Prepare a record for the Rapidfire protocol."""

        # Call the regular Ambient PWS version
        _record = AmbientThread.get_record(self, record, archive)
        # Add the Rapidfire-specific keywords:
        _record['realtime'] = '1'
        _record['rtfreq'] = '2.5'

        return _record

class WOWThread(AmbientThread):
    """Class for posting to the WOW variant of the Ambient protocol."""
    
    # Types and formats of the data to be published:
    _formats = {'dateTime'    : 'dateutc=%s',
                'barometer'   : 'baromin=%.1f',
                'outTemp'     : 'tempf=%.1f',
                'outHumidity' : 'humidity=%.0f',
                'windSpeed'   : 'windspeedmph=%.0f',
                'windDir'     : 'winddir=%.0f',
                'windGust'    : 'windgustmph=%.0f',
                'windGustDir' : 'windgustdir=%.0f',
                'dewpoint'    : 'dewptf=%.1f',
                'hourRain'    : 'rainin=%.2f',
                'dayRain'     : 'dailyrainin=%.2f'}
    
    def format_url(self, record):
        """Return an URL for posting using WOW's version of the Ambient
        protocol."""

        _liststr = ["action=updateraw",
                    "siteid=%s" % self.station,
                    "siteAuthenticationKey=%s" % self.password,
                    "softwaretype=weewx-%s" % weewx.__version__]

        # Go through each of the supported types, formatting it, then adding
        # to _liststr:
        for _key in WOWThread._formats:
            _v = record.get(_key)
            # Check to make sure the type is not null
            if _v is not None :
                if _key == 'dateTime':
                    _v = urllib.quote_plus(datetime.datetime.utcfromtimestamp(_v).isoformat(' '))
                # Format the value, and accumulate in _liststr:
                _liststr.append(WOWThread._formats[_key] % _v)
        # Now stick all the pieces together with an ampersand between them:
        _urlquery = '&'.join(_liststr)
        # This will be the complete URL for the HTTP GET:
        _url = "%s?%s" % (self.server_url, _urlquery)
        return _url

    def post_request(self, request):
        """Version of post_request() for the WOW protocol, which
        uses a response error code to signal a bad login."""
        try:
            try:
                _response = urllib2.urlopen(request, timeout=self.timeout)
            except TypeError:
                _response = urllib2.urlopen(request)
        except urllib2.HTTPError, e:
            # WOW signals a bad login with a HTML Error 400 or 403 code:
            if e.code == 400 or e.code == 403:
                raise weecore.restx.BadLogin(e)
            else:
                raise
        else:
            return _response

#==============================================================================
#                    CWOP
#==============================================================================

class StdCWOP(weecore.restx.StdRESTful):
    """Weewx service for posting using the CWOP protocol.
    
    Manages a separate thread CWOPThread"""

    # A regular expression that matches CWOP stations that
    # don't need a passcode. This will match CW1234, etc.
    valid_prefix_re = re.compile('[C-Z]W+[0-9]+')
    
    # Default list of CWOP servers to try:
    default_servers = ['cwop.aprs.net:14580', 'cwop.aprs.net:23']

    def __init__(self, engine, config_dict):
        
        super(StdCWOP, self).__init__(engine, config_dict)
        
        # Extract the required parameters. If one of them is missing,
        # a KeyError exception will occur. Be prepared to catch it.
        try:
            # Extract a copy of the dictionary with the CWOP options:
            _cwop_dict = accumulateLeaves(config_dict['StdRESTful']['CWOP'], max_level=1)
            _cwop_dict['station'] = _cwop_dict['station'].upper()
            
            # See if this station requires a passcode:
            if re.match(StdCWOP.valid_prefix_re, _cwop_dict['station']):
                _cwop_dict.setdefault('passcode', '-1')
            elif not _cwop_dict.has_key('passcode'):
                raise KeyError('passcode')
        except KeyError, e:
            syslog.syslog(syslog.LOG_DEBUG, "restx: CWOP: Data will not be posted. "
                          "Missing option: %s" % e)
            return

        # Get the database dictionary. Throw away the manager. We don't need it.
        _, _database_dict = weecore.archive.prep_database(config_dict, 'wx_binding')

        _cwop_dict.setdefault('latitude',  self.engine.stn_info.latitude_f)
        _cwop_dict.setdefault('longitude', self.engine.stn_info.longitude_f)
        _cwop_dict.setdefault('station_type', config_dict['Station'].get('station_type', 'Unknown'))
        self.archive_queue = Queue.Queue()
        self.archive_thread = CWOPThread(self.archive_queue, _database_dict,
                                         **_cwop_dict)
        self.archive_thread.start()
        self.bind(weecore.NEW_ARCHIVE_RECORD, self.new_archive_record)
        syslog.syslog(syslog.LOG_INFO, "restx: CWOP: Data for station %s will be posted" % 
                      _cwop_dict['station'])

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)

class CWOPThread(weecore.restx.RESTThread):
    """Concrete class for threads posting from the archive queue,
    using the CWOP protocol."""

    def __init__(self, queue, database_dict, 
                 station, passcode, latitude, longitude, station_type,
                 server_list=StdCWOP.default_servers,
                 post_interval=600, max_backlog=sys.maxint, stale=1800,
                 log_success=True, log_failure=True,
                 timeout=10, max_tries=3, retry_wait=5):

        """
        Initializer for the CWOPThread class.
        
        Required parameters:

          queue: An instance of Queue.Queue where the records will appear.
          
          database_dict: A dictionary holding the weedb database connection
          information. It will be used to open a connection to the archive 
          database.
          
          station: The name of the station. Something like "DW1234".
          
          passcode: Some stations require a passcode.
          
          latitude: Latitude of the station in decimal degrees.
          
          longitude: Longitude of the station in decimal degrees.
          
          station_type: The type of station. Generally, this is the driver
          symbolic name, such as "Vantage".
          
        Optional parameters:
        
          server_list: A list of strings holding the CWOP server name and
          port. Default is ['cwop.aprs.net:14580', 'cwop.aprs.net:23']
          
          log_success: If True, log a successful post in the system log.
          Default is True.
          
          log_failure: If True, log an unsuccessful post in the system log.
          Default is True.
          
          max_backlog: How many records are allowed to accumulate in the queue
          before the queue is trimmed.
          Default is sys.maxint (essentially, allow any number).
          
          max_tries: How many times to try the post before giving up.
          Default is 3
          
          stale: How old a record can be and still considered useful.
          Default is 1800 (a half hour).
          
          post_interval: How long to wait between posts.
          Default is 600 (every 10 minutes).
          
          timeout: How long to wait for the server to respond before giving up.
          Default is 10 seconds.        
        """        
        # Initialize my superclass
        super(CWOPThread, self).__init__(queue,
                                         protocol_name="CWOP",
                                         database_dict=database_dict,
                                         post_interval=post_interval,
                                         max_backlog=max_backlog,
                                         stale=stale,
                                         log_success=log_success,
                                         log_failure=log_failure,
                                         timeout=timeout,
                                         max_tries=max_tries,
                                         retry_wait=retry_wait)
        self.station       = station
        self.passcode      = passcode
        self.server_list   = server_list
        self.latitude      = to_float(latitude)
        self.longitude     = to_float(longitude)
        self.station_type  = station_type

    def process_record(self, record, archive):
        """Process a record in accordance with the CWOP protocol."""
        
        # Get the full record by querying the database ...
        _full_record = self.get_record(record, archive)
        # ... convert to US if necessary ...
        _us_record = weecore.units.to_US(_full_record)
        # ... get the login and packet strings...
        _login = self.get_login_string()
        _tnc_packet = self.get_tnc_packet(_us_record)
        # ... then post them:
        self.send_packet(_login, _tnc_packet)

    def get_login_string(self):
        _login = "user %s pass %s vers weewx %s\r\n" % (
            self.station, self.passcode, weewx.__version__)
        return _login

    def get_tnc_packet(self, record):
        """Form the TNC2 packet used by CWOP."""

        # Preamble to the TNC packet:
        _prefix = "%s>APRS,TCPIP*:" % (self.station,)

        # Time:
        _time_tt = time.gmtime(record['dateTime'])
        _time_str = time.strftime("@%d%H%Mz", _time_tt)

        # Position:
        _lat_str = weeutil.weeutil.latlon_string(self.latitude,
                                                 ('N', 'S'), 'lat')
        _lon_str = weeutil.weeutil.latlon_string(self.longitude,
                                                 ('E', 'W'), 'lon')
        _latlon_str = '%s%s%s/%s%s%s' % (_lat_str + _lon_str)

        # Wind and temperature
        _wt_list = []
        for _obs_type in ['windDir', 'windSpeed', 'windGust', 'outTemp']:
            _v = record.get(_obs_type)
            _wt_list.append("%03d" % _v if _v is not None else '...')
        _wt_str = "_%s/%sg%st%s" % tuple(_wt_list)

        # Rain
        _rain_list = []
        for _obs_type in ['hourRain', 'rain24', 'dayRain']:
            _v = record.get(_obs_type)
            _rain_list.append("%03d" % (_v * 100.0) if _v is not None else '...')
        _rain_str = "r%sp%sP%s" % tuple(_rain_list)

        # Barometer:
        _baro = record.get('altimeter')
        if _baro is None:
            _baro_str = "b....."
        else:
            # While everything else in the CWOP protocol is in US Customary,
            # they want the barometer in millibars.
            _baro_vt = weecore.units.convert((_baro, 'inHg', 'group_pressure'),
                                           'mbar')
            _baro_str = "b%05d" % (_baro_vt[0] * 10.0)

        # Humidity:
        _humidity = record.get('outHumidity')
        if _humidity is None:
            _humid_str = "h.."
        else:
            _humid_str = ("h%02d" % _humidity) if _humidity < 100.0 else "h00"

        # Radiation:
        _radiation = record.get('radiation')
        if _radiation is None:
            _radiation_str = ""
        elif _radiation < 1000.0:
            _radiation_str = "L%03d" % _radiation
        elif _radiation < 2000.0:
            _radiation_str = "l%03d" % (_radiation - 1000)
        else:
            _radiation_str = ""

        # Station equipment
        _equipment_str = ".weewx-%s-%s" % (weewx.__version__, self.station_type)
        
        _tnc_packet = ''.join([_prefix, _time_str, _latlon_str, _wt_str,
                               _rain_str, _baro_str, _humid_str,
                               _radiation_str, _equipment_str, "\r\n"])

        return _tnc_packet

    def send_packet(self, login, tnc_packet):

        # Go through the list of known server:ports, looking for
        # a connection that works:
        for _serv_addr_str in self.server_list:
            
            _server, _port_str = _serv_addr_str.split(":")
            _port = int(_port_str)
            
            # Try each combination up to max_tries times:
            for _count in range(self.max_tries):
                try:
                    # Get a socket connection:
                    _sock = self._get_connect(_server, _port)
                    syslog.syslog(syslog.LOG_DEBUG, "restx: %s: Connected to server %s:%d" % 
                                  (self.protocol_name, _server, _port))
            
                    try:
                        # Send the login ...
                        self._send(_sock, login, 'login')
                        # ... and then the packet
                        self._send(_sock, tnc_packet, 'packet')
                        return
                        
                    finally:
                        _sock.close()
                except weecore.restx.ConnectError, e:
                    syslog.syslog(syslog.LOG_DEBUG, "restx: %s: Attempt #%d to %s:%d. Connection error: %s" %
                                  (self.protocol_name, _count+1, _server, _port, e))
                except weecore.restx.SendError, e:
                    syslog.syslog(syslog.LOG_DEBUG, "restx: %s: Attempt #%d to %s:%d. Socket send error: %s" %
                                  (self.protocol_name, _count+1, _server, _port, e))
        
        # If we get here, the loop terminated normally, meaning we failed all tries
        raise weecore.restx.FailedPost("Tried %d servers %d times each" % (len(self.server_list), self.max_tries))

    def _get_connect(self, server, port):
        """Get a socket connection to a specific server and port."""

        try:
            _sock = socket.socket()
            _sock.connect((server, port))
        except IOError, e:
            # Unsuccessful. Close it in case it was open:
            try:
                _sock.close()
            except:
                pass
            raise weecore.restx.ConnectError(e)
        
        return _sock

    def _send(self, sock, msg, dbg_msg):
        """Send a message to a specific socket."""

        try:
            sock.send(msg)
        except IOError, e:
            # Unsuccessful. Log it and go around again for another try
            raise weecore.restx.SendError("Packet %s; Error %s" % (dbg_msg, e))
        else:
            # Success. Look for response from the server.
            try:
                _resp = sock.recv(1024)
                return _resp
            except IOError, e:
                syslog.syslog(syslog.LOG_DEBUG, "restx: %s: Exception %s (%s) when looking for response to %s packet" %
                              (self.protocol_name, type(e), e, dbg_msg))
                return

#==============================================================================
#                    Station Registry
#==============================================================================

class StdStationRegistry(weecore.restx.StdRESTful):
    """Class for phoning home to register a weewx station.

    To enable this module, add the following to weewx.conf:

    [StdRESTful]
        [[StationRegistry]]
            register_this_station = True

    This will periodically do a http GET with the following information:

        station_url      Should be world-accessible. Used as key.
        description      Brief synopsis of the station
        latitude         Station latitude in decimal
        longitude        Station longitude in decimal
        station_type     The driver name, for example Vantage, FineOffsetUSB
        station_model    The hardware_name property from the driver
        weewx_info       weewx version
        python_info
        platform_info

    The station_url is the unique key by which a station is identified.
    """

    archive_url = 'http://weewx.com/register/register.cgi'

    def __init__(self, engine, config_dict):
        
        super(StdStationRegistry, self).__init__(engine, config_dict)
        
        # Extract the required parameters. If one of them is missing,
        # a KeyError exception will occur. Be prepared to catch it.
        try:
            # Extract a copy of the dictionary with the registry options:
            _registry_dict = accumulateLeaves(config_dict['StdRESTful']['StationRegistry'], max_level=1)
            _registry_dict.setdefault('station_url',
                                      self.engine.stn_info.station_url)
            if _registry_dict['station_url'] is None:
                raise KeyError("station_url")
        except KeyError, e:
            syslog.syslog(syslog.LOG_DEBUG, "restx: StationRegistry: "
                          "Data will not be posted. Missing option %s" % e)
            return

        # Should the service be run?
        if not to_bool(_registry_dict.pop('register_this_station', False)):
            syslog.syslog(syslog.LOG_INFO, "restx: StationRegistry: "
                          "Registration not requested.")
            return

        _registry_dict.setdefault('station_type', config_dict['Station'].get('station_type', 'Unknown'))
        _registry_dict.setdefault('description',   self.engine.stn_info.location)
        _registry_dict.setdefault('latitude',      self.engine.stn_info.latitude_f)
        _registry_dict.setdefault('longitude',     self.engine.stn_info.longitude_f)
        _registry_dict.setdefault('station_model', self.engine.stn_info.hardware)

        self.archive_queue = Queue.Queue()
        self.archive_thread = StationRegistryThread(self.archive_queue,
                                                    **_registry_dict)
        self.archive_thread.start()
        self.bind(weecore.NEW_ARCHIVE_RECORD, self.new_archive_record)
        syslog.syslog(syslog.LOG_INFO, "restx: StationRegistry: "
                      "Station will be registered.")

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)
        
class StationRegistryThread(weecore.restx.RESTThread):
    """Concrete threaded class for posting to the weewx station registry."""
    
    def __init__(self, queue, station_url, latitude, longitude,
                 server_url=StdStationRegistry.archive_url,
                 description="Unknown",
                 station_type="Unknown", station_model="Unknown",
                 post_interval=604800, max_backlog=0, stale=None,
                 log_success=True, log_failure=True,
                 timeout=60, max_tries=3, retry_wait=5):
        """Initialize an instance of StationRegistryThread.
        
        Required parameters:

          queue: An instance of Queue.Queue where the records will appear.

          station_url: An URL used to identify the station. This will be
          used as the unique key in the registry to identify each station.
          
          latitude: Latitude of the staion
          
          longitude: Longitude of the station
          
        Optional parameters:
        
          server_url: The URL of the registry server. 
          Default is 'http://weewx.com/register/register.cgi'
          
          description: A brief description of the station. 
          Default is 'Unknown'
          
          station_type: The type of station. Generally, this is the name of
          the driver used by the station. 
          Default is 'Unknown'
          
          station_model: The hardware model, typically the hardware_name
          property provided by the driver.
          Default is 'Unknown'.
          
          log_success: If True, log a successful post in the system log.
          Default is True.
          
          log_failure: If True, log an unsuccessful post in the system log.
          Default is True.
          
          max_backlog: How many records are allowed to accumulate in the queue
          before the queue is trimmed.
          Default is zero (no backlog at all).
          
          max_tries: How many times to try the post before giving up.
          Default is 3
          
          stale: How old a record can be and still considered useful.
          Default is None (never becomes too old).
          
          post_interval: How long to wait between posts.
          Default is 604800 seconds (1 week).
          
          timeout: How long to wait for the server to respond before giving up.
          Default is 60 seconds.
        """

        super(StationRegistryThread, self).__init__(queue,
                                                    protocol_name='StationRegistry',
                                                    post_interval=post_interval,
                                                    max_backlog=max_backlog,
                                                    stale=stale,
                                                    log_success=log_success,
                                                    log_failure=log_failure,
                                                    timeout=timeout,
                                                    max_tries=max_tries,
                                                    retry_wait=retry_wait)
        self.station_url   = station_url
        self.latitude      = to_float(latitude)
        self.longitude     = to_float(longitude)
        self.server_url    = server_url
        self.description   = weeutil.weeutil.list_as_string(description)
        self.station_type  = station_type
        self.station_model = station_model
        
    def get_record(self, dummy_record, dummy_archive):
        _record = {}
        _record['station_url']   = self.station_url
        _record['description']   = self.description
        _record['latitude']      = self.latitude
        _record['longitude']     = self.longitude
        _record['station_type']  = self.station_type
        _record['station_model'] = self.station_model
        _record['python_info']   = platform.python_version()
        _record['platform_info'] = platform.platform()
        _record['weewx_info']    = weewx.__version__
        _record['usUnits']       = weecore.US
        
        return _record
        
    _formats = {'station_url'   : 'station_url=%s',
                'description'   : 'description=%s',
                'latitude'      : 'latitude=%.4f',
                'longitude'     : 'longitude=%.4f',
                'station_type'  : 'station_type=%s',
                'station_model' : 'station_model=%s',
                'python_info'   : 'python_info=%s',
                'platform_info' : 'platform_info=%s',
                'weewx_info'    : 'weewx_info=%s'}

    def format_url(self, record):
        """Return an URL for posting using the StationRegistry protocol."""
    
        _liststr = []
        for _key in StationRegistryThread._formats:
            v = record[_key]
            if v is not None:
                _liststr.append(urllib.quote_plus(
                        StationRegistryThread._formats[_key] % v, '='))
        _urlquery = '&'.join(_liststr)
        _url = "%s?%s" % (self.server_url, _urlquery)
        return _url
    
    def check_response(self, response):
        """Check the response from a Station Registry post."""
        for line in response:
            # the server replies to a bad post with a line starting with "FAIL"
            if line.startswith('FAIL'):
                raise weecore.restx.FailedPost(line)

#==============================================================================
# AWEKAS
#==============================================================================

class StdAWEKAS(weecore.restx.StdRESTful):
    """Upload data to AWEKAS - Automatisches WEtterKArten System
    http://www.awekas.at

    To enable this module, add the following to weewx.conf:

    [StdRESTful]
        [[AWEKAS]]
            username = AWEKAS_USERNAME
            password = AWEKAS_PASSWORD
    
    The AWEKAS server expects a single string of values delimited by
    semicolons.  The position of each value matters, for example position 1
    is the awekas username and position 2 is the awekas password.

    Positions 1-25 are defined for the basic API:

    Pos1: user (awekas username)
    Pos2: password (awekas password MD5 Hash)
    Pos3: date (dd.mm.yyyy) (varchar)
    Pos4: time (hh:mm) (varchar)
    Pos5: temperature (C) (float)
    Pos6: humidity (%) (int)
    Pos7: air pressure (hPa) (float)
    Pos8: precipitation (rain at this day) (float)
    Pos9: wind speed (km/h) float)
    Pos10: wind direction (degree) (int)
    Pos11: weather condition (int)
            0=clear warning
            1=clear
            2=sunny sky
            3=partly cloudy
            4=cloudy
            5=heavy cloundy
            6=overcast sky
            7=fog
            8=rain showers
            9=heavy rain showers
           10=light rain
           11=rain
           12=heavy rain
           13=light snow
           14=snow
           15=light snow showers
           16=snow showers
           17=sleet
           18=hail
           19=thunderstorm
           20=storm
           21=freezing rain
           22=warning
           23=drizzle
           24=heavy snow
           25=heavy snow showers
    Pos12: warning text (varchar)
    Pos13: snow high (cm) (int) if no snow leave blank
    Pos14: language (varchar)
           de=german; en=english; it=italian; fr=french; nl=dutch
    Pos15: tendency (int)
           -2 = high falling
           -1 = falling
            0 = steady
            1 = rising
            2 = high rising
    Pos16. wind gust (km/h) (float)
    Pos17: solar radiation (W/m^2) (float) 
    Pos18: UV Index (float)
    Pos19: brightness (LUX) (int)
    Pos20: sunshine hours today (float)
    Pos21: soil temperature (degree C) (float)
    Pos22: rain rate (mm/h) (float)
    Pos23: software flag NNNN_X.Y, for example, WLIP_2.15
    Pos24: longitude (float)
    Pos25: latitude (float)

    positions 26-111 are defined for API2
    """

    def __init__(self, engine, config_dict):
        super(StdAWEKAS, self).__init__(engine, config_dict)
        try:
            site_dict = accumulateLeaves(config_dict['StdRESTful']['AWEKAS'], max_level=1)
            site_dict['username']
            site_dict['password']
        except KeyError, e:
            syslog.syslog(syslog.LOG_DEBUG, "restx: AWEKAS: "
                          "Data will not be posted: Missing option %s" % e)
            return
        site_dict.setdefault('latitude', engine.stn_info.latitude_f)
        site_dict.setdefault('longitude', engine.stn_info.longitude_f)
        site_dict.setdefault('language', 'de')

        # Get the database dictionary. Throw away the manager. We don't need it.
        _, site_dict['database_dict'] = weecore.archive.prep_database(config_dict, 'wx_binding')

        self.archive_queue = Queue.Queue()
        self.archive_thread = AWEKASThread(self.archive_queue, **site_dict)
        self.archive_thread.start()
        self.bind(weecore.NEW_ARCHIVE_RECORD, self.new_archive_record)
        syslog.syslog(syslog.LOG_INFO, "restx: AWEKAS: "
                      "Data will be uploaded for user %s" %
                      site_dict['username'])

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)

# For compatibility with some early alpha versions:
AWEKAS = StdAWEKAS

class AWEKASThread(weecore.restx.RESTThread):

    _SERVER_URL = 'http://data.awekas.at/eingabe_pruefung.php'
    _FORMATS = {'barometer'   : '%.3f',
                'outTemp'     : '%.1f',
                'outHumidity' : '%.0f',
                'windSpeed'   : '%.1f',
                'windDir'     : '%.0f',
                'windGust'    : '%.1f',
                'dewpoint'    : '%.1f',
                'hourRain'    : '%.2f',
                'dayRain'     : '%.2f',
                'radiation'   : '%.2f',
                'UV'          : '%.2f',
                'rainRate'    : '%.2f'}

    def __init__(self, queue, username, password, latitude, longitude,
                 database_dict,
                 language='de', server_url=_SERVER_URL, skip_upload=False,
                 post_interval=300, max_backlog=sys.maxint, stale=None,
                 log_success=True, log_failure=True, 
                 timeout=60, max_tries=3, retry_wait=5):
        """Initialize an instances of AWEKASThread.

        Required parameters:

        username: AWEKAS user name

        password: AWEKAS password

        language: Possible values include de, en, it, fr, nl
        Default is de

        latitude: Station latitude in decimal degrees
        Default is station latitude

        longitude: Station longitude in decimal degrees
        Default is station longitude

        Optional parameters:
        
        station: station identifier
        Default is None.

        server_url: URL of the server
        Default is the AWEKAS site
        
        log_success: If True, log a successful post in the system log.
        Default is True.

        log_failure: If True, log an unsuccessful post in the system log.
        Default is True.

        max_backlog: How many records are allowed to accumulate in the queue
        before the queue is trimmed.
        Default is sys.maxint (essentially, allow any number).

        max_tries: How many times to try the post before giving up.
        Default is 3

        stale: How old a record can be and still considered useful.
        Default is None (never becomes too old).

        post_interval: The interval in seconds between posts.
        AWEKAS requests that uploads happen no more often than 5 minutes, so
        this should be set to no less than 300.
        Default is 300

        timeout: How long to wait for the server to respond before giving up.
        Default is 60 seconds

        skip_upload: debugging option to display data but do not upload
        Default is False
        """
        super(AWEKASThread, self).__init__(queue,
                                           protocol_name='AWEKAS',
                                           database_dict=database_dict,
                                           post_interval=post_interval,
                                           max_backlog=max_backlog,
                                           stale=stale,
                                           log_success=log_success,
                                           log_failure=log_failure,
                                           timeout=timeout,
                                           max_tries=max_tries,
                                           retry_wait=retry_wait)
        self.username = username
        self.password = password
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.language = language
        self.server_url = server_url
        self.skip_upload = to_bool(skip_upload)

    def get_record(self, record, archive):
        """Add rainRate to the record."""
        # Get the record from my superclass
        r = super(AWEKASThread, self).get_record(record, archive)
        # Now augment with rainRate, which AWEKAS expects. If the archive does not
        # have rainRate, an exception will be raised. Prepare to catch it.
        try:
            rr = archive.getSql('select rainRate from archive where dateTime=?',
                                (r['dateTime'],))
        except weedb.OperationalError:
            pass
        else:
            r['rainRate'] = rr[0]
        return r

    def process_record(self, record, archive):
        r = self.get_record(record, archive)
        url = self.get_url(r)
        if self.skip_upload:
            syslog.syslog(syslog.LOG_DEBUG, "restx: AWEKAS: skipping upload")
            return
        req = urllib2.Request(url)
        req.add_header("User-Agent", "weewx/%s" % weewx.__version__)
        self.post_with_retries(req)

    def check_response(self, response):
        for line in response:
            if line.startswith("Benutzer/Passwort Fehler"):
                raise weecore.restx.BadLogin(line)
            elif not line.startswith('OK'):
                raise weecore.restx.FailedPost("server returned '%s'" % line)

    def get_url(self, in_record):

        # Convert to units required by awekas
        record = weecore.units.to_METRIC(in_record)
        if record.has_key('dayRain') and record['dayRain'] is not None:
            record['dayRain'] = record['dayRain'] * 10
        if record.has_key('rainRate') and record['rainRate'] is not None:
            record['rainRate'] = record['rainRate'] * 10

        # assemble an array of values in the proper order
        values = [self.username]
        m = hashlib.md5()
        m.update(self.password)
        values.append(m.hexdigest())
        time_tt = time.gmtime(record['dateTime'])
        values.append(time.strftime("%d.%m.%Y", time_tt))
        values.append(time.strftime("%H:%M", time_tt))
        values.append(self._format(record, 'outTemp')) # C
        values.append(self._format(record, 'outHumidity')) # %
        values.append(self._format(record, 'barometer')) # mbar
        values.append(self._format(record, 'dayRain')) # mm
        values.append(self._format(record, 'windSpeed')) # km/h
        values.append(self._format(record, 'windDir'))
        values.append('') # weather condition
        values.append('') # warning text
        values.append('') # snow high
        values.append(self.language)
        values.append('') # tendency
        values.append(self._format(record, 'windGust')) # km/h
        values.append(self._format(record, 'radiation')) # W/m^2
        values.append(self._format(record, 'UV')) # uv index
        values.append('') # brightness in lux
        values.append('') # sunshine hours
        values.append('') # soil temperature
        values.append(self._format(record, 'rainRate')) # mm/h
        values.append('weewx_%s' % weewx.__version__)
        values.append(str(self.longitude))
        values.append(str(self.latitude))

        valstr = ';'.join(values)
        url = self.server_url + '?val=' + valstr
        syslog.syslog(syslog.LOG_DEBUG, 'restx: AWEKAS: url: %s' % url)
        return url

    def _format(self, record, label):
        if record.has_key(label) and record[label] is not None:
            if self._FORMATS.has_key(label):
                return self._FORMATS[label] % record[label]
            return str(record[label])
        return ''
