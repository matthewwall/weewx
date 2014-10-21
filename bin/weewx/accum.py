#
#    Copyright (c) 2009-2014 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
#    $Revision $
#    $Author$
#    $Date$
#
"""Weather-specific specializations to weecore.accum"""

import weecore.accum

def add_wind_value(accum, record, obs_type, add_hilo):
    """Add a single observation of type wind to an accumulator."""

    if obs_type in ['windDir', 'windGust', 'windGustDir']:
        return
    if weecore.debug:
        assert(obs_type == 'windSpeed')
    
    # If the type has not been seen before, initialize it
    accum.init_type('wind')
    # Then add to highs/lows, and to the running sum:
    if add_hilo:
        accum['wind'].addHiLo((record.get('windGust'), record.get('windGustDir')), record['dateTime'])
    accum['wind'].addSum((record['windSpeed'], record.get('windDir')))
        
def wind_extract(accum, record, obs_type):
    """Extract wind values from an accumulator, and put in a record."""
    # Wind records must be flattened into the separate categories:
    record['windSpeed']   = accum[obs_type].avg
    record['windDir']     = accum[obs_type].vec_dir
    record['windGust']    = accum[obs_type].max
    record['windGustDir'] = accum[obs_type].max_dir
        
#===============================================================================
#                            Configuration dictionaries
#===============================================================================

weecore.accum.init_dict['wind'] = weecore.accum.VecStats

weecore.accum.add_record_dict['windSpeed'] = add_wind_value

weecore.accum.extract_dict['wind']      = wind_extract
weecore.accum.extract_dict['rain']      = weecore.accum.Accum.sum_extract
weecore.accum.extract_dict['ET']        = weecore.accum.Accum.sum_extract
weecore.accum.extract_dict['dayET']     = weecore.accum.Accum.last_extract
weecore.accum.extract_dict['monthET']   = weecore.accum.Accum.last_extract
weecore.accum.extract_dict['yearET']    = weecore.accum.Accum.last_extract
weecore.accum.extract_dict['hourRain']  = weecore.accum.Accum.last_extract
weecore.accum.extract_dict['dayRain']   = weecore.accum.Accum.last_extract
weecore.accum.extract_dict['rain24']    = weecore.accum.Accum.last_extract
weecore.accum.extract_dict['monthRain'] = weecore.accum.Accum.last_extract
weecore.accum.extract_dict['yearRain']  = weecore.accum.Accum.last_extract
weecore.accum.extract_dict['totalRain'] = weecore.accum.Accum.last_extract
