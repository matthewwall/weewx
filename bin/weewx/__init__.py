#
#    Copyright (c) 2009, 2010, 2011, 2012, 2013 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
#    $Id$
#
"""Package weewx, containing modules specific to the weewx runtime engine."""
import time

import weecore

__version__="3.0.0a1"

#===============================================================================
#           Backwards compatible exception definitions
#===============================================================================

WeeWxIOError = weecore.InputOutputError
WakeupError = weecore.WakeupError
CRCError = weecore.CRCError
RetriesExceeded = weecore.RetriesExceeded
HardwareError = weecore.HardwareError
UnknownArchiveType = weecore.UnknownArchiveType
UnsupportedFeature = weecore.UnsupportedFeature
ViolatedPrecondition = weecore.ViolatedPrecondition
UninitializedDatabase = weecore.UninitializedDatabase
    
#===============================================================================
#                  Backwards compatible event definitions
#===============================================================================

STARTUP = weecore.STARTUP
PRE_LOOP = weecore.PRE_LOOP
NEW_LOOP_PACKET = weecore.NEW_LOOP_PACKET
CHECK_LOOP = weecore.CHECK_LOOP
END_ARCHIVE_PERIOD = weecore.END_ARCHIVE_PERIOD
NEW_ARCHIVE_RECORD = weecore.NEW_ARCHIVE_RECORD
POST_LOOP = weecore.POST_LOOP

#===============================================================================
#                       Class Event
#===============================================================================
Event = weecore.Event

