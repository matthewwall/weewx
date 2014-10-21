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
is responsible for unpacking any configuration information from weecore.conf, and
supplying any defaults. It sets up the queue. It arranges for any new LOOP or
archive records to be put in the queue. It then launches the thread for the
posting object.
 
When a new LOOP or record arrives, the controlling object puts it in the queue,
to be received by the posting object. The controlling object can tell the
posting object to terminate by putting a 'None' in the queue.
 
The posting object should inherit from class RESTThread. It monitors the queue
and blocks until a new record arrives.

The base class RESTThread has a lot of functionality, so specializing classes
should only have to implement a few functions. In particular, 

 - format_url(self, record). This function takes a record dictionary as an
   argument. It is responsible for formatting it as an appropriate URL. 
   For example, the station registry's version emits strings such as
     http://weecore.com/register/register.cgi?weewx_info=2.6.0a5&python_info= ...
   
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
import httplib
import platform
import socket
import sys
import syslog
import threading
import time
import urllib
import urllib2

import weedb
import weeutil.weeutil
import weecore.wxengine
from weeutil.weeutil import to_int, to_float, to_bool, timestamp_to_string, accumulateLeaves
import weecore.units

class FailedPost(IOError):
    """Raised when a post fails after trying the max number of allowed times"""

class BadLogin(StandardError):
    """Raised when login information is bad or missing."""

class ConnectError(IOError):
    """Raised when unable to get a socket connection."""
    
class SendError(IOError):
    """Raised when unable to send through a socket."""
    
#==============================================================================
#                    Abstract base classes
#==============================================================================

class StdRESTful(weecore.wxengine.StdService):
    """Abstract base class for RESTful weewx services.
    
    Offers a few common bits of functionality."""
        
    def shutDown(self):
        """Shut down any threads"""
        if hasattr(self, 'loop_queue') and hasattr(self, 'loop_thread'):
            StdRESTful.shutDown_thread(self.loop_queue, self.loop_thread)
        if hasattr(self, 'archive_queue') and hasattr(self, 'archive_thread'):
            StdRESTful.shutDown_thread(self.archive_queue, self.archive_thread)

    @staticmethod
    def shutDown_thread(q, t):
        """Function to shut down a thread."""
        if q and t.isAlive():
            # Put a None in the queue to signal the thread to shutdown
            q.put(None)
            # Wait up to 20 seconds for the thread to exit:
            t.join(20.0)
            if t.isAlive():
                syslog.syslog(syslog.LOG_ERR, "restx: Unable to shut down %s thread" % t.name)
            else:
                syslog.syslog(syslog.LOG_DEBUG, "restx: Shut down %s thread." % t.name)

# For backwards compatibility with early v2.6 alphas:
StdRESTbase = StdRESTful

class RESTThread(threading.Thread):
    """Abstract base class for RESTful protocol threads.
    
    Offers a few bits of common functionality."""

    def __init__(self, queue, protocol_name, database_dict=None,
                 post_interval=None, max_backlog=sys.maxint, stale=None, 
                 log_success=True, log_failure=True, 
                 timeout=10, max_tries=3, retry_wait=5):
        """Initializer for the class RESTThread
        Required parameters:

          queue: An instance of Queue.Queue where the records will appear.

          protocol_name: A string holding the name of the protocol.
          
        Optional parameters:
        
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

          retry_wait: How long to wait between retries when failures.
          Default is 5 seconds.
          """    
        # Initialize my superclass:
        threading.Thread.__init__(self, name=protocol_name)
        self.setDaemon(True)

        self.queue         = queue
        self.protocol_name = protocol_name
        self.database_dict = database_dict
        self.log_success   = to_bool(log_success)
        self.log_failure   = to_bool(log_failure)
        self.max_backlog   = to_int(max_backlog)
        self.max_tries     = to_int(max_tries)
        self.stale         = to_int(stale)
        self.post_interval = to_int(post_interval)
        self.timeout       = to_int(timeout)
        self.retry_wait    = to_int(retry_wait)
        self.lastpost = 0

    def get_record(self, record, archive):
        """Augment record data with additional data from the archive.
        Should return results in the same units as the record and the database.
        
        This is a general version that works for:
          - WeatherUnderground
          - PWSweather
          - WOW
          - CWOP
        It can be overridden and specialized for additional protocols.

        returns: A dictionary of weather values"""
        
        _time_ts = record['dateTime']
        _sod_ts = weeutil.weeutil.startOfDay(_time_ts)
        
        # Make a copy of the record, then start adding to it:
        _datadict = dict(record)

        # If the type 'rain' does not appear in the archive schema, an exception will
        # be raised. Be prepared to catch it.
        try:        
            if not _datadict.has_key('hourRain'):
                # CWOP says rain should be "rain that fell in the past hour". WU
                # says it should be "the accumulated rainfall in the past 60 min".
                # Presumably, this is exclusive of the archive record 60 minutes
                # before, so the SQL statement is exclusive on the left, inclusive
                # on the right.
                _result = archive.getSql("SELECT SUM(rain), MIN(usUnits), MAX(usUnits) FROM archive "
                                         "WHERE dateTime>? AND dateTime<=?",
                                         (_time_ts - 3600.0, _time_ts))
                if _result is not None and _result[0] is not None:
                    if not _result[1] == _result[2] == record['usUnits']:
                        raise ValueError("Inconsistent units (%s vs %s vs %s) when querying for hourRain" %
                                         (_result[1], _result[2], record['usUnits']))
                    _datadict['hourRain'] = _result[0]
                else:
                    _datadict['hourRain'] = None
    
            if not _datadict.has_key('rain24'):
                # Similar issue, except for last 24 hours:
                _result = archive.getSql("SELECT SUM(rain), MIN(usUnits), MAX(usUnits) FROM archive "
                                         "WHERE dateTime>? AND dateTime<=?",
                                         (_time_ts - 24*3600.0, _time_ts))
                if _result is not None and _result[0] is not None:
                    if not _result[1] == _result[2] == record['usUnits']:
                        raise ValueError("Inconsistent units (%s vs %s vs %s) when querying for rain24" %
                                         (_result[1], _result[2], record['usUnits']))
                    _datadict['rain24'] = _result[0]
                else:
                    _datadict['rain24'] = None
    
            if not _datadict.has_key('dayRain'):
                # NB: The WU considers the archive with time stamp 00:00
                # (midnight) as (wrongly) belonging to the current day
                # (instead of the previous day). But, it's their site,
                # so we'll do it their way.  That means the SELECT statement
                # is inclusive on both time ends:
                _result = archive.getSql("SELECT SUM(rain), MIN(usUnits), MAX(usUnits) FROM archive "
                                         "WHERE dateTime>=? AND dateTime<=?", 
                                         (_sod_ts, _time_ts))
                if _result is not None and _result[0] is not None:
                    if not _result[1] == _result[2] == record['usUnits']:
                        raise ValueError("Inconsistent units (%s vs %s vs %s) when querying for dayRain" %
                                         (_result[1], _result[2], record['usUnits']))
                    _datadict['dayRain'] = _result[0]
                else:
                    _datadict['dayRain'] = None

        except weedb.OperationalError:
            pass
            
        return _datadict

    def run(self):
        """If there is a database specified, open the database, then call
        run_loop() with the database.  If no database is specified, simply
        call run_loop()."""
        
        # Open up the archive. Use a 'with' statement. This will automatically
        # close the archive in the case of an exception:
        if self.database_dict is not None:
            manager_cls = weeutil.weeutil._get_object(self.manager) if hasattr(self, 'manager') else weecore.archive.Archive 
            with manager_cls.open(self.database_dict) as _archive:
                self.run_loop(_archive)
        else:
            self.run_loop()

    def run_loop(self, archive=None):
        """Runs a continuous loop, waiting for records to appear in the queue,
        then processing them.
        """
        
        while True :
            while True:
                # This will block until something appears in the queue:
                _record = self.queue.get()
                # A None record is our signal to exit:
                if _record is None:
                    return
                # If packets have backed up in the queue, trim it until it's
                # no bigger than the max allowed backlog:
                if self.queue.qsize() <= self.max_backlog:
                    break
    
            if self.skip_this_post(_record['dateTime']):
                continue
    
            try:
                # Process the record, using whatever method the specializing
                # class provides
                self.process_record(_record, archive)
            except BadLogin, e:
                syslog.syslog(syslog.LOG_ERR, "restx: %s: bad login; "
                              "waiting 60 minutes then retrying" % self.protocol_name)
                time.sleep(3600)
            except FailedPost, e:
                if self.log_failure:
                    _time_str = timestamp_to_string(_record['dateTime'])
                    syslog.syslog(syslog.LOG_ERR, "restx: %s: Failed to publish record %s: %s" 
                                  % (self.protocol_name, _time_str, e))
            except Exception, e:
                # Some unknown exception occurred. This is probably a serious
                # problem. Exit.
                syslog.syslog(syslog.LOG_CRIT, "restx: %s: Unexpected exception of type %s" % 
                              (self.protocol_name, type(e)))
                syslog.syslog(syslog.LOG_CRIT, "restx: %s: Thread exiting. Reason: %s" % 
                              (self.protocol_name, e))
                return
            else:
                if self.log_success:
                    _time_str = timestamp_to_string(_record['dateTime'])
                    syslog.syslog(syslog.LOG_INFO, "restx: %s: Published record %s" % 
                                  (self.protocol_name, _time_str))

    def process_record(self, record, archive):
        """Default version of process_record.
        
        This version uses HTTP GETs to do the post, which should work for many
        protocols, but it can always be replaced by a specializing class."""
        
        # Get the full record by querying the database ...
        _full_record = self.get_record(record, archive)
        # ... convert to US if necessary ...
        _us_record = weecore.units.to_US(_full_record)
        # ... format the URL, using the relevant protocol ...
        _url = self.format_url(_us_record)
        # ... convert to a Request object ...
        _request = urllib2.Request(_url)
        _request.add_header("User-Agent", "weewx/%s" % weecore.__version__)
        # ... then, finally, post it
        self.post_with_retries(_request)

    def post_with_retries(self, request):
        """Post a request, retrying if necessary
        
        Attempts to post the request object up to max_tries times. 
        Catches a set of generic exceptions.
        
        request: An instance of urllib2.Request
        """

        # Retry up to max_tries times:
        for _count in range(self.max_tries):
            try:
                # Do a single post. The function post_request() can be
                # specialized by a RESTful service to catch any unusual
                # exceptions.
                _response = self.post_request(request)
                if _response.code == 200:
                    # No exception thrown and we got a good response code, but
                    # we're still not done.  Some protocols encode a bad
                    # station ID or password in the return message.
                    # Give any interested protocols a chance to examine it.
                    # This must also be inside the try block because some
                    # implementations defer hitting the socket until the
                    # response is used.
                    self.check_response(_response)
                    # Does not seem to be an error. We're done.
                    return
                else:
                    # We got a bad response code. Log it and try again.
                    syslog.syslog(syslog.LOG_DEBUG, "restx: %s: Failed upload attempt %d: Code %s" % 
                                  (self.protocol_name, _count+1, _response.code))
            except (urllib2.URLError, socket.error, httplib.BadStatusLine, httplib.IncompleteRead), e:
                # An exception was thrown. Log it and go around for another try
                syslog.syslog(syslog.LOG_DEBUG, "restx: %s: Failed upload attempt %d: Exception %s" % 
                              (self.protocol_name, _count+1, e))
            time.sleep(self.retry_wait)
        else:
            # This is executed only if the loop terminates normally, meaning
            # the upload failed max_tries times. Raise an exception. Caller
            # can decide what to do with it.
            raise FailedPost("Failed upload after %d tries" % (self.max_tries,))

    def post_request(self, request):
        """Post a request object. This version does not catch any HTTP
        exceptions.
        
        Specializing versions can can catch any unusual exceptions that might
        get raised by their protocol.
        """
        try:
            # Python 2.5 and earlier do not have a "timeout" parameter.
            # Including one could cause a TypeError exception. Be prepared
            # to catch it.
            _response = urllib2.urlopen(request, timeout=self.timeout)
        except TypeError:
            # Must be Python 2.5 or early. Use a simple, unadorned request
            _response = urllib2.urlopen(request)
        return _response

    def check_response(self, response):
        """Check the response from a HTTP post. This version does nothing."""
        pass
    
    def skip_this_post(self, time_ts):
        """Check whether the post is current"""
        # Don't post if this record is too old
        if self.stale is not None:
            _how_old = time.time() - time_ts
            if _how_old > self.stale:
                syslog.syslog(syslog.LOG_DEBUG, "restx: %s: record %s is stale (%d > %d)." %
                              (self.protocol_name, timestamp_to_string(time_ts), 
                               _how_old, self.stale))
                return True
 
        if self.post_interval is not None:
            # We don't want to post more often than the post interval
            _how_long = time_ts - self.lastpost
            if _how_long < self.post_interval:
                syslog.syslog(syslog.LOG_DEBUG, 
                              "restx: %s: wait interval (%d < %d) has not passed for record %s" % 
                              (self.protocol_name,
                               _how_long, self.post_interval,
                               timestamp_to_string(time_ts)))
                return True
    
        self.lastpost = time_ts
        return False



#==============================================================================
#                    Station Registry
#==============================================================================

class StdStationRegistry(StdRESTful):
    """Class for phoning home to register a weewx station.

    To enable this module, add the following to weecore.conf:

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

    archive_url = 'http://weecore.com/register/register.cgi'

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
        
class StationRegistryThread(RESTThread):
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
          Default is 'http://weecore.com/register/register.cgi'
          
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
        _record['weewx_info']    = weecore.__version__
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
                raise FailedPost(line)

