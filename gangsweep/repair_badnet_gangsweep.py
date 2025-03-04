#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Dec 21 22:06:42 2020

@author: raskshithakoriraj
"""
#import torch
import h5py
import numpy as np
import sys
from tensorflow import keras
from tensorflow.keras import backend as K
import tensorflow as tf
import os as os

tf.enable_eager_execution()
TF_CONFIG_ = tf.compat.v1.ConfigProto()
TF_CONFIG_.gpu_options.allow_growth = True
sess = tf.compat.v1.Session(config = TF_CONFIG_)

#clean_data_filename='data/clean_test_data.h5'
#validation_data_filename = 'data/clean_validation_data.h5'
#model_filename = 'models/anonymous_bd_net.h5'
#repaired_model_filename = "models/anonymous_bd_net_latest_repaired.h5"
if len(sys.argv) != 5:
    print(
"""USAGE:
    python repair_badnet_gangsweep.py [clean_data/validation_data_h5_location] [validation_data_h5_location] [model_h5_location] [repaired_model_h5_location]
 Example:
    python repair_badnet_gangsweep.py data/clean_test_data.h5 data/clean_validation_data.h5 models/anonymous_bd_net.h5  models/anonymous_bd_net_latest_repaired.h5    
"""
    )
    sys.exit()
clean_data_filename=str(sys.argv[1])
validation_data_filename = str(sys.argv[2])
model_filename = str(sys.argv[3])
repaired_model_filename = str(sys.argv[4])


try:
    os.mkdir(os.path.join('models'))
except:
    print("Models already exists")

try:
    os.mkdir(os.path.join('models','trigger_gen'))
except:
     print("models/trigger_gen already exists")

epochs = 4
target_begin = 0

target_end = 1283

filter_size = 16

no_res = 2
success_rate_treshold = 0.80

create_new_triggers = True

# Prepare the training dataset.
batch_size = 64
pois_file = h5py.File("bad_net_poise_data.h5", "w")
def res_block_gen(model, kernal_size, filters, strides):
    
    gen = model
    
    model = keras.layers.Conv2D(filters = filters, kernel_size = kernal_size, strides = strides, padding = "same")(model)
    model = keras.layers.BatchNormalization(momentum = 0.5)(model)
    # Using Parametric ReLU
    model = keras.layers.PReLU(alpha_initializer='zeros', alpha_regularizer=None, alpha_constraint=None, shared_axes=[1,2])(model)
    model = keras.layers.Conv2D(filters = filters, kernel_size = kernal_size, strides = strides, padding = "same")(model)
    model = keras.layers.BatchNormalization(momentum = 0.5)(model)
        
    model = keras.layers.add([gen, model])
    
    return model


def data_loader(filepath):
    data = h5py.File(filepath, 'r')
    x_data = np.array(data['data'],dtype='float32')
    y_data = np.array(data['label'],dtype='float32')
    x_data = x_data.transpose((0,2,3,1))

    return x_data, y_data

@tf.function
def data_preprocess(x_data):
    return x_data/255


def get_gan_layers(gen_x,no_res = 2 , filters=32):
    #gen_x = keras.Input(shape=(55, 47, 3), name='input')
        # feature extraction
    conv_1 = keras.layers.Conv2D(filters, 9, padding='same', name='conv_1')(gen_x)
    prelu_1 = keras.layers.PReLU(alpha_initializer='zeros', alpha_regularizer=None, alpha_constraint=None, shared_axes=[1,2])(conv_1)
    res = res_block_gen(prelu_1, 3, filters, 1)
    for i in range(1,no_res):
        res = res_block_gen(res, 3, filters, 1)
    
    conv_2 = keras.layers.Conv2D(filters = filters, kernel_size = 3, strides = 1, padding = "same")(res)
    batch_1 = keras.layers.BatchNormalization(momentum = 0.5)(conv_2)
    model = keras.layers.add([prelu_1, batch_1])
            
    conv_3 = keras.layers.Conv2D(filters = 1, kernel_size = 9, strides = 1, padding = "same")(model)
    act_1 = keras.layers.Activation('tanh')(conv_3)
    return act_1

def fine_tune(x_test,y_test,target):
#training just the new layer
    bd_base_model = keras.models.load_model(repaired_model_filename)
    bd_base_model.trainable = False
    new_output = keras.layers.Dense(1284, activation='softmax',name='output')(bd_base_model.layers[-2].output)
    bd_model = keras.Model(inputs=bd_base_model.inputs, outputs=new_output)
    bd_model.compile(
        optimizer=keras.optimizers.Adadelta(1),
        loss=bd_model_original.loss,
        metrics=['accuracy']
        )
    bd_model.fit(x_test,y_test,shuffle=True, batch_size=64,epochs=7)
    
    #Retrining the whole
    bd_model.trainable = True
    
    bd_model.compile(
        optimizer=keras.optimizers.Adadelta(1e-4),
        loss=bd_model_original.loss,
        metrics=['accuracy']
        )
    bd_model.fit(x_test,y_test,shuffle=True, batch_size=64,epochs=2)
    
    bd_model.save(repaired_model_filename)
    bd_model.save("models/anonymous_bd_net_latest_repaired_after_{}.h5")
    del bd_model

gen_x = keras.Input(shape=(55, 47, 3), name='input')       
model_gan = keras.models.Model(inputs = gen_x, outputs = get_gan_layers(gen_x,no_res,filter_size))
model_gan.save("gan_orig.h5")
#model_gan.summary()
#keras.utils.plot_model(model_gan, to_file='gan_model_architecture.png')

x_train, y_train = data_loader(clean_data_filename)
x_train = data_preprocess(x_train)

train_dataset = tf.data.Dataset.from_tensor_slices((x_train, y_train))
train_dataset = train_dataset.shuffle(buffer_size=x_train.shape[0],seed=90).batch(batch_size)
bd_model_original = keras.models.load_model(model_filename)
bd_model_original.save(repaired_model_filename)
bd_model_original.Training = False


x_valid, y_valid = data_loader(validation_data_filename)
    
x_valid = data_preprocess(x_valid)

for target in range(target_begin,target_end):
    
    K.clear_session()
    sess.close()
    sess = tf.compat.v1.Session(config = TF_CONFIG_)
    K.set_session(sess)
    
    #model_gan.set_weights(original_weights)
    if create_new_triggers:
        model_gan = keras.models.load_model("gan_orig.h5")
        optimizer = tf.keras.optimizers.Adam(learning_rate=0.0001)
        for epoch in range(epochs):
            print("\nStart of epoch %d" % (epoch,))
            for step, (x_batch_train, y_batch_train) in enumerate(train_dataset):
                with tf.GradientTape() as tape:                
                    logits = model_gan(x_batch_train, training=True)  # Logits for this minibatch                    
                    #Scale between 0 and 1
                    epsilon = 1e-12 
                    logits = tf.div(
                                tf.subtract(logits, tf.reduce_min(logits)),
                                tf.math.maximum( 
                                    tf.subtract(tf.reduce_max(logits), tf.reduce_min(logits)),epsilon)
                            )
                    
                    #Perturbation Loss
                    l_pert = 0.01*tf.reduce_mean(tf.reduce_sum(tf.norm(logits,axis=(1,2)),axis=1))
                    
                    y_predict = bd_model_original(
                        tf.clip_by_value(x_batch_train+logits,0,1), 
                        training=False)
                    target_pred =  y_predict[:,target]
                    if target == 0 :
                        k_th_pred = tf.math.reduce_max(y_predict[:,1:],axis=1)
                    if target == 1282 :
                        k_th_pred = tf.math.reduce_max(y_predict[:,:1282],axis=1)
                    else:
                        k_th_pred = tf.math.maximum(tf.math.reduce_max(y_predict[:,:target],axis=1),
                                                    tf.math.reduce_max(y_predict[:,target+1:],axis=1))
                     
                    l_adv = tf.math.reduce_mean(tf.math.maximum(k_th_pred- target_pred, 0))
                    a = 2.0
                    if tf.is_nan(l_adv).numpy():
                        l_adv=0
    
                    if tf.is_nan(l_pert).numpy():
                        l_pert=0
    
                    if epoch > 0 and l_pert > l_adv:
                        a = 0.5
    
                    
                    loss_value =(a * l_adv) + l_pert
                    
                    
                grads = tape.gradient(loss_value, model_gan.trainable_weights)
                optimizer.apply_gradients(zip(grads, model_gan.trainable_weights))
                if step % 50 == 0:
                    print(
                        "Training loss (for one batch) at step {}: target:{} l_adv:{} l_pert:{} loss:{} ".format(step,target,l_adv,l_pert,loss_value)
                    )
                    print("Seen so far: %s samples" % ((step + 1) * batch_size))
        
        model_gan.save("models/trigger_gen/trigger_gen_{}.h5".format(target))
        del optimizer
    # Calcualte success rate
    
    model_gan = keras.models.load_model("models/trigger_gen/trigger_gen_{}.h5".format(target))
    
    #x_valid, y_valid = data_loader(validation_data_filename)
    
    #x_valid = data_preprocess(x_valid)
    y_valid_10_arg = np.random.choice(tf.where(y_valid!=target).numpy().flatten(),int(y_valid.shape[0]*0.1))
    x_valid_10 =  x_valid.numpy()[y_valid_10_arg,:]
    y_valid_10 =  y_valid[y_valid_10_arg]
    logits = model_gan(
        x_valid_10,
        training=False
    )
    #Scale between 0 and 1
    epsilon = 1e-12 
    logits = tf.div(
        tf.subtract(logits, tf.reduce_min(logits)),
        tf.math.maximum( 
            tf.subtract(tf.reduce_max(logits), tf.reduce_min(logits)),epsilon)
        )
    y_valid_pred = tf.argmax(bd_model_original(
                    tf.clip_by_value(x_valid_10+logits,0,1), 
                    training=False),axis = 1)
    success_rate = int(np.argwhere(y_valid_pred.numpy()==target).shape[0])/ int(y_valid_pred.shape[0]) 
    print("target {} ;success_rate {}".format(target,success_rate))
    if(success_rate>success_rate_treshold):
         x_test_poisoned = x_valid_10 + logits
         x_test_poisoned =  x_test_poisoned.numpy()[np.argwhere(y_valid_pred.numpy()==target).flatten(),:]
         pois_file.create_dataset("pois_{}".format(target), data =x_test_poisoned )
         x_test = np.vstack((x_train, x_test_poisoned))
         y_test = np.hstack((y_train, np.zeros(x_test_poisoned.shape[0])+1283))
         fine_tune(x_test,y_test,target)
         
    del model_gan
     


x_test_poisoned = []
pois_file = h5py.File("bad_net_poise_data.h5", "r")
for data in list(pois_file.values()):
    x_test_poisoned.append(np.array(data))
x_test_poisoned = np.vstack(x_test_poisoned)
x_test = np.vstack((x_train, x_test_poisoned))
y_test = np.hstack((y_train, np.zeros(x_test_poisoned.shape[0])+1283))
fine_tune(x_test,y_test,target+1)

pois_file.close()
